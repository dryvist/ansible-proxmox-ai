"""A registry value written anywhere but the registry is a build failure.

The registry rule (AGENTS.md, llm_router): every model id and alias is written
ONCE, in llm-models.d/, and the role, its tests and the playbooks DERIVE it. The
render-parity guard catches a name that is rendered but unregistered; it cannot
catch the opposite input — a REGISTERED id re-typed as a literal — because that
input renders correctly today and drifts silently the day the registry entry
changes. This scan is what fails on that input.

Two zones, because the rule has two sides:

* The PROJECTION zone — roles/llm_router, tests/llm_router, playbooks, and
  every Python test's string constants — may not carry any registry value at
  all. The one exception is not hand-listed:
  a value the inventory assigns literally (`hermes_brain_model: hermes-default`)
  is the consumer-selection contract, and a test fixture supplying that same
  input is mirroring the inventory, not re-typing the registry. The set is
  computed from inventory/group_vars, so deriving a selector there removes
  the exemption on its own.
* The CONSUMER zone — every other role's defaults/vars, inventory, the other
  tests — calls the fabric by a client-facing name. It may name a
  client_model_id or an alias; it may never name an upstream-only id, because
  that is the physical backend the router exists to hide.

Parsed YAML values and Python string constants are scanned, never raw text: a
comment or docstring naming a model is prose, not a re-type. A value counts
when a scalar IS the value, ends in
`/<value>` (a provider-prefixed form), or carries it as a quoted token inside a
Jinja expression (`selectattr(..., 'equalto', '<value>')`, `['<value>', ...]`).
Unquoted prose inside a message is not matched.

Role-name aliases (stable_aliases on a non-servable entry — the seeded DB
roles, 55-roles.yml) are excluded from the alias set by the registry's own
`servable` flag, not by a list here: they are role names, not model_group
aliases, and the role seed necessarily carries the name.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

PROJECTION_GLOBS = ("roles/llm_router/**/*.yml", "tests/llm_router/**/*.yml", "playbooks/**/*.yml", "tests/**/*.py")
CONSUMER_GLOBS = ("roles/*/defaults/**/*.yml", "roles/*/vars/**/*.yml", "inventory/group_vars/**/*.yml", "tests/**/*.yml")


class _Permissive(yaml.SafeLoader):
    """Ansible files carry tags SafeLoader rejects (!vault, !unsafe); keep the node's text."""


_Permissive.add_multi_constructor("!", lambda loader, suffix, node: getattr(loader, "construct_" + node.id)(node))


def registry_values(root: Path) -> dict[str, set[str]]:
    """Every registry value -> the kinds it occurs as ({client, upstream, alias})."""
    values: dict[str, set[str]] = {}
    for slice_file in sorted((root / "llm-models.d").glob("*.yml")):
        for entries in yaml.safe_load(slice_file.read_text()).values():
            for entry in entries:
                values.setdefault(str(entry["client_model_id"]), set()).add("client")
                values.setdefault(str(entry["upstream_model_id"]), set()).add("upstream")
                if entry.get("servable"):
                    for alias in entry.get("stable_aliases") or []:
                        values.setdefault(str(alias), set()).add("alias")
    return values


def _scalars(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _scalars(value, path + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _scalars(value, path + (str(index),))
    elif isinstance(node, str):
        yield path, node


_QUOTED = re.compile(r"""['"]([^'"\n]+)['"]""")


def names(scalar: str, value: str) -> bool:
    """Does this scalar spell `value` as a literal (bare, provider-prefixed, or quoted in an expression)?"""
    if scalar == value or scalar.endswith("/" + value):
        return True
    return any(q == value or q.endswith("/" + value) for q in _QUOTED.findall(scalar))


def _literals(path: Path):
    """(location, string) for every literal in a YAML document or a Python source file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        for doc in yaml.load_all(text, Loader=_Permissive):
            for key_path, scalar in _scalars(doc):
                yield ".".join(key_path[-3:]), scalar
        return
    tree = ast.parse(text)
    docstrings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings:
            yield f"line {node.lineno}", node.value


def _hits(root: Path, globs, values):
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            if "llm-models.d" in path.parts or not path.is_file():
                continue
            for where, literal in _literals(path):
                for value in values:
                    if names(literal, value):
                        yield f"{path.relative_to(root)} [{where}] spells '{value}'"


def inventory_literals(root: Path, values) -> set[str]:
    """Registry values the inventory assigns as bare literals: the consumer-selection contract."""
    found = set()
    for path in sorted(root.glob("inventory/group_vars/**/*.yml")):
        for _, scalar in _scalars(yaml.load(path.read_text(encoding="utf-8"), Loader=_Permissive)):
            if scalar in values:
                found.add(scalar)
    return found


def scan(root: Path) -> tuple[list[str], list[str]]:
    """(projection-zone offenders, consumer-zone offenders) for a checkout."""
    values = registry_values(root)
    projection_values = set(values) - inventory_literals(root, values)
    upstream_only = {v for v, kinds in values.items() if kinds == {"upstream"}}
    projection = sorted(set(_hits(root, PROJECTION_GLOBS, projection_values)))
    consumer = sorted(set(_hits(root, CONSUMER_GLOBS, upstream_only)) - set(projection))
    return projection, consumer


def test_registry_loads_something():
    """Anti-vacuity: an empty registry would make both scans pass with nothing scanned."""
    values = registry_values(REPO_ROOT)
    assert len(values) >= 10, f"only {len(values)} registry values loaded; the loader is broken"
    assert any("alias" in kinds for kinds in values.values()), "no servable alias loaded"
    assert any(kinds == {"upstream"} for kinds in values.values()), "no upstream-only id loaded"


def test_projection_zone_carries_no_registry_literal():
    offenders, _ = scan(REPO_ROOT)
    assert not offenders, (
        "Registry values re-typed where they must be derived (llm_router role, its "
        "tests, playbooks, Python tests). Use the registry projection — "
        "llm_router_primary_model, llm_router_model_group_aliases, the tier lists — "
        "or read llm-models.d/ and select by serving_role/tier, never the literal:\n  "
        + "\n  ".join(offenders)
    )


def test_consumer_zone_names_no_upstream_only_id():
    _, offenders = scan(REPO_ROOT)
    assert not offenders, (
        "A consumer names a physical upstream id the router exists to hide. Call "
        "the client_model_id or an alias:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_can_see_a_planted_literal(tmp_path: Path):
    """Anti-vacuity: a clean tree must mean 'none found', not 'nothing matched'."""
    (tmp_path / "llm-models.d").mkdir()
    (tmp_path / "llm-models.d" / "10.yml").write_text(
        "_r:\n"
        "  - {client_model_id: org/planted-model, upstream_model_id: org/planted-model,"
        " servable: true, stable_aliases: [planted-alias]}\n"
        "  - {client_model_id: cloud-rung, upstream_model_id: vendor/physical-name}\n"
    )
    defaults = tmp_path / "roles/llm_router/defaults/main"
    defaults.mkdir(parents=True)
    (defaults / "x.yml").write_text(
        "bare: org/planted-model\n"
        "prefixed: openai/org/planted-model\n"
        "expr: \"{{ aliases['planted-alias'] }}\"\n"
        "prose_msg: planted-alias must not resolve to org/planted-model\n"
        "# comment: org/planted-model\n"
        "derived: '{{ llm_router_primary_model }}'\n"
    )
    other = tmp_path / "roles/other/defaults"
    other.mkdir(parents=True)
    (other / "main.yml").write_text("model: vendor/physical-name\nfine: cloud-rung\n")
    py = tmp_path / "tests/other"
    py.mkdir(parents=True)
    (py / "test_y.py").write_text(
        '"""Docstring naming org/planted-model is prose."""\n'
        "# a comment naming planted-alias is prose\n"
        "EXPECTED = {'planted-alias': 'x'}\n"
        "MESSAGE = \"fallbacks=['org/planted-model']\"\n"
    )
    projection, consumer = scan(tmp_path)
    assert [h.split("[")[1].split("]")[0] for h in projection] == [
        "bare", "expr", "prefixed", "line 3", "line 4",
    ], projection
    assert consumer == ["roles/other/defaults/main.yml [model] spells 'vendor/physical-name'"], consumer


def test_inventory_literal_is_the_contract_not_a_retype(tmp_path: Path):
    """A fixture mirroring an inventory-assigned selector is exempt; derive it in inventory and it is not."""
    (tmp_path / "llm-models.d").mkdir()
    (tmp_path / "llm-models.d" / "10.yml").write_text(
        "_r:\n  - {client_model_id: fabric-router, upstream_model_id: complexity_router}\n"
    )
    inv = tmp_path / "inventory/group_vars"
    inv.mkdir(parents=True)
    fixture = tmp_path / "tests/llm_router"
    fixture.mkdir(parents=True)
    (fixture / "test_x.yml").write_text("- hosts: localhost\n  vars:\n    brain: fabric-router\n")

    (inv / "all.yml").write_text("brain: fabric-router\n")
    assert scan(tmp_path) == ([], [])

    (inv / "all.yml").write_text("brain: '{{ llm_router_hermes_virtual_models[0].client_model_id }}'\n")
    projection, _ = scan(tmp_path)
    assert projection == ["tests/llm_router/test_x.yml [0.vars.brain] spells 'fabric-router'"], projection


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
