"""Shared fixtures for the repo-crawl pre-check script's self-checks."""
from __future__ import annotations

import json
import types
from pathlib import Path

from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
TEMPLATE = (ROLE / "templates" / "hermes-repo-crawl.py.j2").read_text()

DEFAULT_CTX = {
    "ansible_managed": "managed",
    "hermes_agent_hermes_home": "/tmp/hermes-repo-crawl-test-home",
    "hermes_agent_repo_crawl_repos": [{"repo": "dryvist/widget", "boundary": "public"}],
    "hermes_agent_github_app_slug": "jacobs-hermes-agent",
    "hermes_agent_repo_crawl_max_open_prs": 3,
}


def render(**overrides) -> str:
    env = Environment()
    env.filters["to_json"] = json.dumps
    env.filters["comment"] = lambda s: ""
    ctx = {**DEFAULT_CTX, **overrides}
    rendered = env.from_string(TEMPLATE).render(**ctx)
    assert "{{" not in rendered, "repo-crawl template left an unrendered Jinja expression"
    return rendered


def load_module(**overrides):
    mod = types.ModuleType("hermes_repo_crawl")
    exec(compile(render(**overrides), str(ROLE / "templates" / "hermes-repo-crawl.py.j2"), "exec"), mod.__dict__)
    return mod
