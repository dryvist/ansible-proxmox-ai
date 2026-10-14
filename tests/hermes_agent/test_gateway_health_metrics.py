"""The gateway config enables Hermes' native OTLP gateway-health metrics, and nothing else of it.

Only the long-running gateway config carries the block. Profile configs and the
standalone public gateway do not render it. Diagnostic spans and warning/error
logs stay off, so the export is metrics only.
"""

from __future__ import annotations

from _role_files import role_defaults
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


def test_profile_and_public_gateway_configs_do_not_export_it() -> None:
    for template in ("config-profile.yaml.j2", "config-public-gateway.yaml.j2"):
        _, config = _render(template, {})
        assert "monitoring" not in config, template
