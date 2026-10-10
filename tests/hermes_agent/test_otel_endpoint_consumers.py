"""ai_orchestration_otel_endpoint reaches every consumer that exports to it.

The shared OTLP endpoint is set once, in inventory/group_vars/all.yml. Each
consumer below is rendered with a sentinel in its place, and the sentinel must
come out the other end. A consumer that drops the endpoint, or hard-codes its
own, fails here instead of in a trace that never arrives.

Consumers take the endpoint two ways. Some templates read it directly; the rest
read a role default that is itself "{{ ai_orchestration_otel_endpoint }}". For
those the default is rendered first, so a default replaced by a literal is caught.
"""

from __future__ import annotations

import pytest
from _role_files import template_text
from _shell_harness_shared import ROLES, defaults_of, render

SENTINEL = "http://otel-sentinel.test.invalid:4318"

CONSUMERS = [
    pytest.param("llm_router", "litellm.env.j2", None, id="llm_router"),
    pytest.param("agent_exec", "otel_bootstrap.py.j2", None, id="agent_exec"),
    pytest.param("dify_docker", "docker-compose.yml.j2", None, id="dify_docker"),
    pytest.param(
        "langgraph_docker", "otel_bootstrap.py.j2", "langgraph_docker_otel_endpoint", id="langgraph_docker"
    ),
    pytest.param(
        "hindsight_docker", "docker-compose.yml.j2", "hindsight_docker_otel_endpoint", id="hindsight_docker"
    ),
    pytest.param("hermes_agent", "hermes-env.j2", "hermes_agent_otel_endpoint", id="hermes_agent"),
    pytest.param("open_webui", "open-webui.env.j2", "open_webui_otel_endpoint", id="open_webui"),
    pytest.param(
        "agentgateway_docker",
        "config.yaml.j2",
        "agentgateway_docker_otlp_endpoint",
        id="agentgateway_docker",
    ),
]


# llm_router's model lists are projections of the llm-models.d registry, which a
# plain render cannot evaluate. The endpoint line does not depend on which models
# are listed, so they render empty rather than re-deriving the registry here.
REGISTRY_PROJECTIONS = {
    "llm_router_openrouter_models": [],
    "llm_router_opencode_models": [],
    "llm_router_hermes_cloud_models_credentialed": [],
    "llm_router_zai_models": [],
}


@pytest.mark.parametrize(("role", "template", "default"), CONSUMERS)
def test_consumer_carries_the_orchestration_endpoint(role: str, template: str, default: str | None) -> None:
    defaults = defaults_of(role)
    context = {
        **defaults,
        **REGISTRY_PROJECTIONS,
        "ansible_managed": "test render",
        "ai_orchestration_otel_endpoint": SENTINEL,
    }
    if default is not None:
        context[default] = render(defaults[default], {"ai_orchestration_otel_endpoint": SENTINEL})
    rendered = render(template_text(ROLES / role, template), context, lenient=True)
    assert SENTINEL in rendered, f"{role}/{template} does not carry ai_orchestration_otel_endpoint"
