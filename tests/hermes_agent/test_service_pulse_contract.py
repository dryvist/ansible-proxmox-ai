"""Check the pulse contract against captured 2026-10-08 endpoint output."""

import json
from pathlib import Path

from ansible.plugins.filter.core import regex_replace
from jinja2 import Environment, StrictUndefined

from _role_files import role_defaults, role_tasks

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
FIXTURES = Path(__file__).parent / "fixtures" / "service-pulse"
DEFAULTS = role_defaults(ROLE)
PULSE_PROMPT = next(
    task["ansible.builtin.set_fact"]["hermes_agent_service_pulse_cron_prompt"]
    for task in role_tasks(ROLE, "prompt_catalog.yml")
    if "hermes_agent_service_pulse_cron_prompt" in task.get("ansible.builtin.set_fact", {})
)
SPLUNK_PROMPT = next(
    task["ansible.builtin.set_fact"]["hermes_agent_splunk_health_cron_prompt"]
    for task in role_tasks(ROLE, "prompt_catalog.yml")
    if "hermes_agent_splunk_health_cron_prompt" in task.get("ansible.builtin.set_fact", {})
)

PULSE_CATALOG = """You are the Hermes service pulse.

Use the memory tools available from the configured Hermes provider. Keep continuity compact when using builtin memory.

CONFIGURED INCIDENT BASELINE: use the operator-maintained value.
"""

SPLUNK_CATALOG = """You are the Splunk health reviewer.

Use the memory tools available from the configured Hermes provider.
LOOK with `run_splunk_query` only.
"""


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_router_readiness_probe_matches_the_current_response_shape():
    response = _fixture("router-readiness.json")
    assert response == {"status": "healthy", "db": "connected"}
    endpoints = DEFAULTS["hermes_agent_service_pulse_endpoints"]
    assert "health/readiness" in endpoints
    assert "deployment" not in endpoints.lower()
    assert "only HTTP 200 means a health endpoint is healthy" in endpoints
    assert "401 or 406 proves reachability only" in " ".join(endpoints.split())
    assert "memory service" not in endpoints


def _render_prompt(template, provider, catalog_body, baseline=182):
    environment = Environment(autoescape=False, undefined=StrictUndefined)
    environment.filters["regex_replace"] = regex_replace
    context = {
        **DEFAULTS,
        "hermes_agent_prompts_path": "/fixture",
        "hermes_agent_memory_provider": provider,
        "hermes_agent_service_pulse_open_incident_baseline": baseline,
        "hermes_agent_service_pulse_endpoints": environment.from_string(
            DEFAULTS["hermes_agent_service_pulse_endpoints"]
        ).render(lookup=lambda *_: "example.test"),
        "hermes_agent_service_pulse_hindsight_endpoint": environment.from_string(
            DEFAULTS["hermes_agent_service_pulse_hindsight_endpoint"]
        ).render(lookup=lambda *_: "example.test"),
    }
    environment.globals["lookup"] = lambda _plugin, _path, **_options: catalog_body
    return environment.from_string(template).render(**context)


def test_incident_baseline_is_rendered_from_config_and_compared_to_live_total():
    response = _fixture("zammad-open-total.json")
    configured = DEFAULTS["hermes_agent_service_pulse_open_incident_baseline"]
    assert response == {"total_count": 188}
    for baseline in (configured, response["total_count"], 0):
        rendered = _render_prompt(PULSE_PROMPT, "hindsight", PULSE_CATALOG, baseline)
        assert rendered.startswith(PULSE_CATALOG.strip())
        rendered_endpoints = Environment(autoescape=False).from_string(
            DEFAULTS["hermes_agent_service_pulse_endpoints"]
        ).render(lookup=lambda *_: "example.test")
        assert rendered_endpoints.strip() in rendered
        assert "memory service https://hindsight.example.test/health" in rendered
        assert rendered.endswith(f"OPEN-INCIDENT BASELINE (operator-maintained): {baseline}")


def test_builtin_service_pulse_keeps_provider_neutral_catalog_and_omits_hindsight_health():
    rendered = _render_prompt(PULSE_PROMPT, "builtin", PULSE_CATALOG)
    assert PULSE_CATALOG.strip() in rendered
    assert "Hindsight" not in rendered
    assert "memory service https://hindsight" not in rendered


def test_service_pulse_health_endpoint_is_provider_specific():
    hindsight = _render_prompt(PULSE_PROMPT, "hindsight", PULSE_CATALOG)
    builtin = _render_prompt(PULSE_PROMPT, "builtin", PULSE_CATALOG)
    assert "memory service https://hindsight.example.test/health" in hindsight
    assert "memory service https://hindsight.example.test/health" not in builtin


def test_daily_status_adds_hindsight_health_endpoint_only_for_hindsight_provider():
    endpoint_template = Environment(autoescape=False, undefined=StrictUndefined)
    endpoint_template.globals["lookup"] = lambda *_args, **_kwargs: "example.test"
    daily = DEFAULTS["hermes_agent_daily_status_endpoints"]

    hindsight = endpoint_template.from_string(daily).render(
        **{**DEFAULTS, "hermes_agent_memory_provider": "hindsight"}
    )
    builtin = endpoint_template.from_string(daily).render(
        **{**DEFAULTS, "hermes_agent_memory_provider": "builtin"}
    )

    assert "https://hindsight.example.test/health" in hindsight
    assert "https://hindsight.example.test/health" not in builtin
    assert "only HTTP 200 means healthy" in " ".join(builtin.split())
    assert "5xx means unhealthy" in " ".join(builtin.split())
    assert "401 or 406 means reachable, not healthy" in " ".join(builtin.split())


def test_splunk_health_prompt_adapts_to_builtin_memory():
    hindsight = _render_prompt(SPLUNK_PROMPT, "hindsight", SPLUNK_CATALOG)
    builtin = _render_prompt(SPLUNK_PROMPT, "builtin", SPLUNK_CATALOG)
    assert hindsight == builtin
    assert SPLUNK_CATALOG.strip() in builtin


if __name__ == "__main__":
    checks = [
        test_router_readiness_probe_matches_the_current_response_shape,
        test_incident_baseline_is_rendered_from_config_and_compared_to_live_total,
        test_builtin_service_pulse_keeps_provider_neutral_catalog_and_omits_hindsight_health,
        test_service_pulse_health_endpoint_is_provider_specific,
        test_splunk_health_prompt_adapts_to_builtin_memory,
    ]
    for check in checks:
        check()
        print(f"ok  {check.__name__}")
    print(f"\n{len(checks)} checks passed")
