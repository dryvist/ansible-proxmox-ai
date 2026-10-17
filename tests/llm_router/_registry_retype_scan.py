"""Shared implementation for registry retype scan tests."""

from __future__ import annotations

import ast
import re
from pathlib import Path

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
        for name, entries in yaml.safe_load(slice_file.read_text()).items():
            if name.startswith("_llm_model_artifacts") or name.endswith("_artifacts"):
                for artifact in entries:
                    values.setdefault(str(artifact["hf_repo"]), set()).add("upstream")
                continue
            for entry in entries:
                if "client_model_id" not in entry:
                    continue
                values.setdefault(str(entry["client_model_id"]), set()).add("client")
                if "upstream_model_id" in entry:
                    values.setdefault(str(entry["upstream_model_id"]), set()).add("upstream")
                if entry.get("route_group"):
                    values.setdefault(str(entry["route_group"]), set()).add("route_group")
                # A stable_alias counts as an "alias" value on a servable
                # entry (unchanged) or an OpenRouter ZDR entry that declares
                # one. Its context window is now read from homelab-contracts,
                # so this raw-YAML scan uses the categorical ZDR/alias fields
                # rather than duplicating the numeric threshold. The
                # hermes-router and embedding carve-outs are deliberately
                # NOT mirrored here yet: their alias names collide with
                # common-word literals elsewhere in the tree (see A5 PR
                # notes) and widening this set for them is exactly the
                # explosion the `servable`-only rule was chosen to avoid.
                is_alias_bearing = entry.get("servable") or (
                    entry.get("tier") == "openrouter"
                    and entry.get("stable_aliases")
                    and entry.get("zero_data_retention", False) is True
                )
                if is_alias_bearing:
                    for alias in entry.get("stable_aliases") or []:
                        values.setdefault(str(alias), set()).add("alias")
                for alias in entry.get("direct_route_aliases") or []:
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
    # A dict-literal key is a field name (e.g. the llama_cpp per-model schema's
    # `"embeddings"` boolean flag), never a re-typed registry VALUE — mirrors
    # _scalars() above, which recurses into YAML mapping values only and never
    # treats a mapping key as a scanned literal.
    dict_keys = {k for n in ast.walk(tree) if isinstance(n, ast.Dict) for k in n.keys if isinstance(k, ast.Constant)}
    # A Python subscript index is a field name (for example, the `default`
    # configuration key), not a model value.
    subscript_keys = {
        n.slice for n in ast.walk(tree)
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
    }
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node not in docstrings and node not in dict_keys and node not in subscript_keys):
            yield f"line {node.lineno}", node.value


def _hits(root: Path, globs, values, contextual_values=frozenset()):
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            if "llm-models.d" in path.parts or not path.is_file():
                continue
            for where, literal in _literals(path):
                for value in values:
                    if names(literal, value) and (
                        value not in contextual_values
                        or _model_value_context(path, where, literal)
                    ):
                        yield f"{path.relative_to(root)} [{where}] spells '{value}'"


def _model_value_context(path: Path, where: str, scalar: str) -> bool:
    """Only treat alias values as re-types when used in model-related fields.

    Many existing configuration words also happen to be aliases. This keeps
    profile names and unrelated role values out of the scan while still
    catching aliases in model selectors, alias maps, and fallback chains.
    """
    if re.search(r"selectattr\(['\"]serving_role['\"]", scalar):
        return False
    context = f"{where} {scalar}".lower()
    if re.search(r"\b(model|models|alias|aliases|fallback|fallbacks|route)\b", context):
        return True
    if path.suffix == ".py":
        line_number = int(where.split()[1])
        line = path.read_text(encoding="utf-8").splitlines()[line_number - 1]
        return bool(re.search(r"\b(model|models|alias|aliases|fallback|fallbacks|route)\b", line))
    return False


def inventory_literals(root: Path, values) -> set[str]:
    """Registry values selected by inventory, including its Hermes fallback literal."""
    found = set()
    for path in sorted(root.glob("inventory/group_vars/**/*.yml")):
        for key_path, scalar in _scalars(yaml.load(path.read_text(encoding="utf-8"), Loader=_Permissive)):
            if scalar in values:
                found.add(scalar)
            elif key_path[-1:] == ("hermes_brain_model",):
                found.update(value for value in _QUOTED.findall(scalar) if value in values)
    return found


def scan(root: Path) -> tuple[list[str], list[str]]:
    """(projection-zone offenders, consumer-zone offenders) for a checkout."""
    values = registry_values(root)
    projection_values = set(values) - inventory_literals(root, values)
    alias_values = {value for value, kinds in values.items() if "alias" in kinds}
    route_group_values = {value for value, kinds in values.items() if "route_group" in kinds}
    upstream_only = {v for v, kinds in values.items() if kinds == {"upstream"}}
    projection = sorted(set(_hits(
        root, PROJECTION_GLOBS, projection_values, alias_values | route_group_values
    )))
    consumer = sorted(set(_hits(root, CONSUMER_GLOBS, upstream_only)) - set(projection))
    return projection, consumer
