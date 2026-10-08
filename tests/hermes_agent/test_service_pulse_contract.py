"""Check the pulse contract against captured 2026-10-08 endpoint output."""

import json
from pathlib import Path

from _role_files import role_defaults, role_tasks_text

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
FIXTURES = Path(__file__).parent / "fixtures" / "service-pulse"
DEFAULTS = role_defaults(ROLE)
PROMPT_CATALOG_TASKS = role_tasks_text(ROLE, "prompt_catalog.yml")


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
    assert configured == 182
    assert response["total_count"] - configured == 6
    assert "OPEN-INCIDENT BASELINE (operator-maintained): " in PROMPT_CATALOG_TASKS
    assert "hermes_agent_service_pulse_open_incident_baseline | string" in PROMPT_CATALOG_TASKS


if __name__ == "__main__":
    checks = [
        test_router_readiness_probe_matches_the_current_response_shape,
        test_incident_baseline_is_rendered_from_config_and_compared_to_live_total,
    ]
    for check in checks:
        check()
        print(f"ok  {check.__name__}")
    print(f"\n{len(checks)} checks passed")
