"""Hermes agent deployments use LiteLLM's OpenAI-compatible server provider."""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "roles/llm_router/templates"
AGENT_NAMES = ("hermes-agent", "hermes-private", "hermes-donna")


def _render_agents() -> list[dict]:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
        trim_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(
        pattern, replacement, str(value)
    )
    agents = {
        name: {
            "api_url": f"http://{name}.example.test:8642/v1",
            "model_name": name,
        }
        for name in AGENT_NAMES
    }
    rendered = env.get_template("model-list-hermes-agents.yaml.j2").render(
        llm_router_hermes_agents=agents,
        llm_router_hermes_agent_identities={name: name for name in AGENT_NAMES},
        llm_router_hermes_identity_bearers={name: "placeholder" for name in AGENT_NAMES},
        llm_router_hermes_agent_timeout=120,
        llm_router_hermes_agent_context_window=32768,
    )
    return yaml.safe_load("model_list:\n" + rendered)["model_list"]


def test_hermes_agents_use_hosted_vllm_provider_prefix():
    deployments = _render_agents()

    assert {
        deployment["model_name"]: deployment["litellm_params"]["model"]
        for deployment in deployments
    } == {name: f"hosted_vllm/{name}" for name in AGENT_NAMES}
