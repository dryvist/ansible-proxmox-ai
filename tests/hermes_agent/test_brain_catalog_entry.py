"""hermes_agent_brain_catalog_entry resolves when hermes_brain_model is a
router capability name.

hermes_brain_model (inventory/group_vars/all/00-core.yml) names a capability, which is
a registry route_group, not a catalog alias. This renders the real expression
through Jinja against the registry's enabled entries, so a brain model with no
catalog entry fails here instead of in the Hermes converge ("No first item,
sequence was empty"). The catalog is built from the registry's own ids, so the
test names no model.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, StrictUndefined, UndefinedError

from _registry import load_registry
from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"


def _render_brain_entry(brain: str, registry: list[dict]) -> dict:
    defaults = role_defaults(ROLE)
    catalog = [
        {"name": e["client_model_id"], "context_window": 1000 + i, "max_output_tokens": 100 + i}
        for i, e in enumerate(registry)
    ]
    ctx = {
        "hermes_brain_model": brain,
        "llm_model_catalog_models": catalog,
        "llm_router_registry_enabled": [e for e in registry if e.get("enabled")],
    }
    rendered = Environment(autoescape=False, undefined=StrictUndefined).from_string(  # noqa: S701
        defaults["hermes_agent_brain_catalog_entry"]
    ).render(ctx)
    return ast.literal_eval(rendered)


def test_brain_capability_resolves_to_its_enabled_registry_entry() -> None:
    brain = yaml.safe_load((REPO_ROOT / "inventory/group_vars/all/00-core.yml").read_text())[
        "hermes_brain_model"
    ]
    registry = load_registry()
    expected = [
        e["client_model_id"]
        for e in registry
        if e.get("enabled") and e.get("route_group") == brain
    ]
    assert expected, f"no enabled registry entry has route_group {brain!r}"
    entry = _render_brain_entry(brain, registry)
    assert entry["name"] in expected
    assert entry["context_window"] and entry["max_output_tokens"]


def test_a_name_that_is_not_a_route_group_does_not_resolve() -> None:
    with pytest.raises(UndefinedError, match="No first item"):
        _render_brain_entry("not-a-capability", load_registry())
