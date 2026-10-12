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


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_router_readiness_probe_matches_the_current_response_shape():
    response = _fixture("router-readiness.json")
    assert response == {"status": "healthy", "db": "connected"}
    endpoints = DEFAULTS["hermes_agent_service_pulse_endpoints"]
    assert "health/readiness" in endpoints
    assert "deployment" not in endpoints.lower()


def test_incident_baseline_is_rendered_from_config_and_compared_to_live_total():
    response = _fixture("zammad-open-total.json")
    configured = DEFAULTS["hermes_agent_service_pulse_open_incident_baseline"]
    assert response == {"total_count": 188}
    environment = Environment(autoescape=False, undefined=StrictUndefined)
    environment.filters["regex_replace"] = regex_replace
    catalog_body = "---\ntitle: Service pulse\n---\nProbe the configured services.\n"

    def lookup(plugin, path, **options):
        assert plugin == "ansible.builtin.file"
        assert path == "/fixture/" + DEFAULTS["hermes_agent_service_pulse_cron_prompt_file"]
        assert options == {"rstrip": False}
        return catalog_body

    for baseline in (configured, response["total_count"], 0):
        rendered = environment.from_string(PULSE_PROMPT).render(
            **{
                **DEFAULTS,
                "hermes_agent_prompts_path": "/fixture",
                "hermes_agent_service_pulse_open_incident_baseline": baseline,
                "lookup": lookup,
            }
        )
        assert rendered.startswith("Probe the configured services.\n\n")
        assert DEFAULTS["hermes_agent_service_pulse_endpoints"].strip() in rendered
        assert rendered.endswith(f"OPEN-INCIDENT BASELINE (operator-maintained): {baseline}")


if __name__ == "__main__":
    checks = [
        test_router_readiness_probe_matches_the_current_response_shape,
        test_incident_baseline_is_rendered_from_config_and_compared_to_live_total,
    ]
    for check in checks:
        check()
        print(f"ok  {check.__name__}")
    print(f"\n{len(checks)} checks passed")
