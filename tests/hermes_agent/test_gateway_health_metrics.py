"""The gateway config enables Hermes' native OTLP gateway-health metrics, and nothing else of it.

Only the long-running gateway config carries the block. Profile configs and the
standalone public gateway do not render it. Diagnostic spans and warning/error
logs stay off, so the export is metrics only.
"""

from __future__ import annotations

from _role_files import role_defaults, role_tasks
from _shell_harness_shared import defaults_of
from test_context_limits import ROLE_ROOT, _render


def test_gateway_config_exports_gateway_health_metrics_only() -> None:
    defaults = role_defaults(ROLE_ROOT)
    _, config = _render("config.yaml.j2", {})
    monitoring = config["monitoring"]

    # Hermes rewrites the /v1/traces suffix to /v1/metrics for the metrics exporter.
    assert monitoring["export"]["otlp"] == {
        "enabled": True,
        "endpoint": f"{defaults['hermes_agent_otel_endpoint']}/v1/traces",
    }
    assert monitoring["gateway_health_export"] == {
        "enabled": True,
        "metrics_enabled": True,
        "diagnostic_events_enabled": False,
        "warning_error_events_enabled": False,
        "export_interval_seconds": defaults["hermes_agent_gateway_health_metrics_interval_seconds"],
    }


def test_native_endpoint_default_is_the_orchestration_endpoint() -> None:
    # Read raw: a literal here would bypass the shared OTLP endpoint every consumer reads.
    assert defaults_of("hermes_agent")["hermes_agent_otel_endpoint"] == "{{ ai_orchestration_otel_endpoint }}"


def test_otlp_packages_are_installed_so_the_export_can_start() -> None:
    # Without them the gateway logs "No module named 'opentelemetry.exporter'" and exports nothing.
    installs = [t for t in role_tasks(ROLE_ROOT) if t.get("name") == "Install the OTLP export packages into the Hermes venv"]
    assert len(installs) == 1
    task = installs[0]
    cmd = task["ansible.builtin.command"]["cmd"]
    version = "{{ hermes_agent_otlp_sdk_version }}"
    assert f"opentelemetry-sdk=={version}" in cmd
    assert f"opentelemetry-exporter-otlp-proto-http=={version}" in cmd
    # An editable install is reinstalled by uv on every run, so it would restart the gateway every converge.
    assert " -e " not in cmd
    assert "hermes_agent_otel_endpoint" in task["when"]
    assert task["notify"] == "Restart hermes-gateway"
    assert role_defaults(ROLE_ROOT)["hermes_agent_otlp_sdk_version"]


def test_profile_and_public_gateway_configs_do_not_export_it() -> None:
    for template in ("config-profile.yaml.j2", "config-public-gateway.yaml.j2"):
        _, config = _render(template, {})
        assert "monitoring" not in config, template
