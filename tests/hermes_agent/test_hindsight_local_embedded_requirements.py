"""The Hindsight plugin's requirements are exact pins; local_embedded adds hindsight-embed, not hindsight-all."""

from __future__ import annotations

import ast

from jinja2 import Environment

from _role_files import role_defaults
from test_profile_contract import ROLE_ROOT


def _rendered_defaults() -> dict:
    defaults = role_defaults(ROLE_ROOT)
    env = Environment(autoescape=False)
    for key in ("hermes_agent_hindsight_client_pin", "hermes_agent_hindsight_embed_pin"):
        defaults[key] = env.from_string(defaults[key]).render(**defaults)
    return defaults


def _requirements(defaults: dict, mode: str) -> list[str]:
    hindsight = next(p for p in defaults["hermes_agent_plugins_core"] if p["name"] == "hindsight")
    rendered = Environment(autoescape=False).from_string(hindsight["requirements"]).render(
        **{**defaults, "hermes_agent_memory_mode": mode}
    )
    return ast.literal_eval(rendered.strip())


def test_local_external_needs_only_the_pinned_client() -> None:
    defaults = _rendered_defaults()
    assert _requirements(defaults, "local_external") == [f"hindsight-client{defaults['hermes_agent_hindsight_client_pin']}"]


def test_local_embedded_adds_the_pinned_embed_daemon_and_never_hindsight_all() -> None:
    defaults = _rendered_defaults()
    requirements = _requirements(defaults, "local_embedded")
    assert requirements == [
        f"hindsight-client{defaults['hermes_agent_hindsight_client_pin']}",
        f"hindsight-embed{defaults['hermes_agent_hindsight_embed_pin']}",
    ]
    assert all("==" in requirement for requirement in requirements)
    assert not any(requirement.startswith("hindsight-all") for requirement in requirements)
