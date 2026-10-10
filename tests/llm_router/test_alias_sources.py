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

Production sources only: roles, playbooks and inventory. Tests plant their
own fixtures, and the negative cases below plant into a scratch tree.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_GLOBS = ("roles/**/*.yml", "playbooks/**/*.yml", "inventory/**/*.yml")
ALIAS_KEYS = {"stable_aliases", "model_group_alias"}


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
