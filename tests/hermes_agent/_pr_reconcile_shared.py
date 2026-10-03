"""Shared fixtures for the pr-reconcile script's self-checks.

The script is a Jinja template (hermes-pr-reconcile.py.j2) with a dict-literal
config block, not the single-line `NAME = ... {{ var }}` style the splunk
digest fixtures substitute by regex — so this renders it through a real Jinja
Environment instead, stubbing only the Ansible-specific filters (`comment`,
`to_json`, `bool`) that bare Jinja2 does not ship.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
TEMPLATE = (ROLE / "templates" / "hermes-pr-reconcile.py.j2").read_text()

DEFAULT_CTX = {
    "ansible_managed": "managed",
    "hermes_agent_github_route_org": "dryvist",
    "hermes_agent_github_app_slug": "jacobs-hermes-agent",
    "hermes_agent_hermes_home": "/tmp/hermes-pr-reconcile-test-home",
    "hermes_agent_webhook_port": 8644,
    "hermes_agent_github_route_public_enabled": True,
    "hermes_agent_github_route_public_name": "github-public",
    "hermes_agent_github_route_private_enabled": True,
    "hermes_agent_github_route_private_name": "github-private",
    "hermes_agent_pr_reconcile_cap": 10,
}


def render(**overrides) -> str:
    env = Environment()
    env.filters["to_json"] = json.dumps
    env.filters["bool"] = bool
    env.filters["comment"] = lambda s: ""
    ctx = {**DEFAULT_CTX, **overrides}
    rendered = env.from_string(TEMPLATE).render(**ctx)
    assert "{{" not in rendered, "pr-reconcile template left an unrendered Jinja expression"
    return rendered


def load_module(**overrides):
    mod = types.ModuleType("hermes_pr_reconcile")
    exec(compile(render(**overrides), str(ROLE / "templates" / "hermes-pr-reconcile.py.j2"), "exec"), mod.__dict__)
    return mod
