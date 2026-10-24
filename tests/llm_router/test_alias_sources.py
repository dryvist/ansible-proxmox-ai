"""Model aliases and role targets are declared only in the registry.

The registry rule (AGENTS.md, llm_router) puts every alias on its registry
entry's `stable_aliases`. test_registry_retype_scan.py fails on a registered
VALUE typed a second time; this scan fails on the other input — a new alias
DECLARED somewhere else, which no registry entry knows about:

* a `stable_aliases` or `model_group_alias` key outside llm-models.d/;
* a model-alias variable (name contains `alias`, not a virtual-key alias)
  whose value is a literal instead of a projection of the registry;
* a role deployment (a mapping with `role` and `model`) whose target or
  fallbacks are literals instead of projections.

Membership rule (lane contract): every lane and legacy name in
LANE_VOCABULARY must be served by the registry, except PENDING_NAMES, and a
literal `*_model` selection in inventory/ must name a registry name. The
vocabulary is mirrored in homelab-contracts (tests/llm_roles/check_refs.yml)
and nix-ai (lib/checks/litellm-local-aliases.nix); change all three together.

Production sources only: roles, playbooks and inventory. Tests plant their
own fixtures, and the negative cases below plant into a scratch tree.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_GLOBS = ("roles/**/*.yml", "playbooks/**/*.yml", "inventory/**/*.yml")
ALIAS_KEYS = {"stable_aliases", "model_group_alias"}

# Lane vocabulary, identical in homelab-contracts and nix-ai (see module doc).
LANE_VOCABULARY = (
    "auto",
    "light",
    "standard",
    "critical",
    "long",
    "embed",
    "rerank",
    "ocr",
    "classify",
    "judge",
    "small",
    "fast",
    "cheap",
    "medium",
    "default",
    "large",
    "best",
    # Existing caller role names outside the lane vocabulary, kept until the
    # operator decides their lane.
    "coding",
    "recorder",
)
# Vocabulary names with no router target yet. Reported here, never silently
# passed: rerank has no reranker role, classify has no classifier target.
PENDING_NAMES = frozenset({"rerank", "classify"})


class _Permissive(yaml.SafeLoader):
    """Ansible files carry tags SafeLoader rejects (!vault, !unsafe); keep the node's text."""


_Permissive.add_multi_constructor("!", lambda loader, suffix, node: getattr(loader, "construct_" + node.id)(node))


def _projected(value: object) -> bool:
    """A projection is a Jinja expression; an empty value declares nothing."""
    if isinstance(value, str):
        return "{{" in value or not value.strip()
    if isinstance(value, list):
        return all(_projected(item) for item in value)
    return value in (None, {})


def _walk(node: object, where: str, problems: list[str]) -> None:
    if isinstance(node, dict):
        for key in ALIAS_KEYS & set(node):
            problems.append(f"{where}: `{key}` declares an alias outside llm-models.d/")
        if {"role", "model"} <= set(node):
            for field in ("model", "fallbacks"):
                if not _projected(node.get(field)):
                    problems.append(f"{where}: role `{node['role']}` has a literal `{field}`")
        for value in node.values():
            _walk(value, where, problems)
    elif isinstance(node, list):
        for item in node:
            _walk(item, where, problems)


def alias_source_problems(root: Path) -> list[str]:
    problems: list[str] = []
    for pattern in SCAN_GLOBS:
        for path in sorted(root.glob(pattern)):
            where = str(path.relative_to(root))
            for doc in yaml.load_all(path.read_text(), Loader=_Permissive):
                if isinstance(doc, dict) and "/tasks/" not in f"/{where}":
                    for name, value in doc.items():
                        if "alias" in str(name) and "key_alias" not in str(name) and not _projected(value):
                            problems.append(f"{where}: `{name}` is a literal, not a registry projection")
                _walk(doc, where, problems)
    return problems


def test_aliases_are_declared_only_in_the_registry() -> None:
    assert alias_source_problems(REPO_ROOT) == []


def _plant(tmp_path: Path, text: str) -> Path:
    planted = tmp_path / "roles/llm_router/defaults/main/99-planted.yml"
    planted.parent.mkdir(parents=True)
    planted.write_text(text)
    return tmp_path


def test_planted_stable_alias_fails(tmp_path: Path) -> None:
    root = _plant(tmp_path, "llm_router_extra:\n  - client_model_id: x\n    stable_aliases: [planted]\n")
    assert alias_source_problems(root) == [
        "roles/llm_router/defaults/main/99-planted.yml: `stable_aliases` declares an alias outside llm-models.d/"
    ]


def test_planted_alias_map_fails(tmp_path: Path) -> None:
    root = _plant(tmp_path, "llm_router_planted_aliases:\n  planted: some-model\n")
    assert alias_source_problems(root) == [
        "roles/llm_router/defaults/main/99-planted.yml: `llm_router_planted_aliases` is a literal, not a registry projection"
    ]


def test_planted_literal_role_target_fails(tmp_path: Path) -> None:
    root = _plant(tmp_path, "llm_router_role_deployments:\n  - role: planted\n    model: some-model\n    fallbacks: []\n")
    assert alias_source_problems(root) == [
        "roles/llm_router/defaults/main/99-planted.yml: role `planted` has a literal `model`"
    ]


def test_projected_values_pass(tmp_path: Path) -> None:
    root = _plant(
        tmp_path,
        "llm_router_x_aliases: '{{ registry | map(attribute=\"a\") }}'\n"
        "llm_router_rotate_key_aliases: [operator-key]\n"
        "llm_router_role_deployments:\n  - role: r\n    model: '{{ m }}'\n    fallbacks: ['{{ f }}']\n",
    )
    assert alias_source_problems(root) == []


def registry_names(root: Path) -> set[str]:
    """Every name a caller can address: client ids, route groups, aliases, DB roles."""
    names: set[str] = set()
    for path in sorted((root / "llm-models.d").glob("*.yml")):
        doc = yaml.load(path.read_text(), Loader=_Permissive) or {}
        for value in doc.values():
            for entry in value if isinstance(value, list) else []:
                if not isinstance(entry, dict):
                    continue
                names.update(str(entry[key]) for key in ("client_model_id", "route_group") if key in entry)
                for key in ("stable_aliases", "direct_route_aliases"):
                    names.update(str(name) for name in entry.get(key) or [])
    roles = root / "roles/llm_router/defaults/main/55-roles.yml"
    if roles.exists():
        doc = yaml.load(roles.read_text(), Loader=_Permissive) or {}
        names.update(str(d["role"]) for d in doc.get("_llm_router_role_deployments_source", []))
    return names


def unserved_vocabulary(root: Path) -> list[str]:
    served = registry_names(root)
    return sorted(name for name in LANE_VOCABULARY if name not in PENDING_NAMES and name not in served)


def literal_selection_problems(root: Path) -> list[str]:
    """A literal `*_model` selection in inventory must name a registry name."""
    served = registry_names(root)
    problems: list[str] = []
    for path in sorted((root / "inventory").glob("**/*.yml")):
        where = str(path.relative_to(root))
        for doc in yaml.load_all(path.read_text(), Loader=_Permissive):
            for key, value in (doc.items() if isinstance(doc, dict) else []):
                if str(key).endswith("_model") and isinstance(value, str):
                    if "{{" not in value and value.strip() and value.strip() not in served:
                        problems.append(f"{where}: `{key}` selects `{value.strip()}`, not a registry name")
    return problems


def test_lane_vocabulary_is_served_by_the_registry() -> None:
    assert unserved_vocabulary(REPO_ROOT) == []


def test_literal_model_selections_name_registry_entries() -> None:
    assert literal_selection_problems(REPO_ROOT) == []


def test_planted_unserved_lane_name_fails(tmp_path: Path) -> None:
    # An empty registry serves nothing, so every vocabulary name is reported.
    assert "light" in unserved_vocabulary(tmp_path)
    assert "rerank" not in unserved_vocabulary(tmp_path)


def test_planted_unregistered_selection_fails(tmp_path: Path) -> None:
    planted = tmp_path / "inventory/group_vars/99-planted.yml"
    planted.parent.mkdir(parents=True)
    planted.write_text("planted_probe_model: lane-not-served\nprojected_probe_model: '{{ x }}'\n")
    assert literal_selection_problems(tmp_path) == [
        "inventory/group_vars/99-planted.yml: `planted_probe_model` selects `lane-not-served`, not a registry name"
    ]
