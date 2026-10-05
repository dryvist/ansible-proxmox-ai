"""Contract for the standalone github-public gateway: config, env, unit, tasks."""
import json
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from _role_files import role_defaults, role_tasks, template_text

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
HOME = "/home/h/.hermes/profiles/review-oss"


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(ROLE / "templates"), trim_blocks=True)
    env.filters["to_json"] = json.dumps
    env.filters["bool"] = bool
    env.filters["comment"] = lambda s: ""
    env.filters["mandatory"] = lambda v, msg="": v
    return env


def _ctx() -> dict:
    ctx = {k: v for k, v in DEFAULTS.items() if k.startswith(("hermes_agent_github_", "hermes_agent_unit_"))}
    ctx.update(
        ansible_managed="managed",
        hermes_agent_hermes_home="/home/h/.hermes",
        hermes_agent_github_public_gateway_home=HOME,
        hermes_agent_github_public_gateway_model="review-model",
        hermes_agent_github_public_gateway_cmd="/opt/hermes gateway run",
        hermes_agent_github_route_bot_login="example-bot[bot]",
        hermes_agent_github_route_public_enabled=True,
        hermes_agent_model_provider=DEFAULTS["hermes_agent_model_provider"],
        hermes_agent_model_base_url="http://router.invalid/v1",
        hermes_agent_model_api_mode="chat_completions",
        hermes_agent_model_context_length=65536,
        hermes_agent_model_max_tokens=8192,
        hermes_agent_log_level="INFO",
        hermes_agent_max_turns=90,
        hermes_agent_disabled_toolsets=["x"],
        hermes_agent_timezone="UTC",
        hermes_agent_user="hermes",
        hermes_agent_github_app_slug="example-app",
        hermes_agent_webhook_secret="whsec",
        hermes_agent_ca_bundle_path="/etc/ssl/ca.pem",
        bao_apps_secrets={"hermes_review_oss_llm_router_key": "router-key"},
    )
    return ctx


def _config() -> dict:
    return yaml.safe_load(_env().get_template("config-public-gateway.yaml.j2").render(**_ctx()))


def _by_name(tasks: list, name: str) -> dict:
    return next(t for t in tasks if t["name"] == name)


def test_defaults_name_a_port_distinct_from_the_default_gateway():
    assert DEFAULTS["hermes_agent_github_public_gateway_profile"] == "review-oss"
    assert DEFAULTS["hermes_agent_github_public_gateway_port"] == 8645
    assert DEFAULTS["hermes_agent_github_public_gateway_model"] == ""
    assert "hermes_agent_github_route_public_profile" not in DEFAULTS


def test_config_serves_only_the_public_route():
    cfg = _config()
    assert cfg["model"]["provider"] == "custom"
    webhook = cfg["platforms"]["webhook"]
    assert list(cfg["platforms"]) == ["webhook"]
    assert webhook["extra"]["port"] == 8645
    assert list(webhook["extra"]["routes"]) == ["github-public"]
    route = webhook["extra"]["routes"]["github-public"]
    filters = {f["field"]: f for f in route["filters"]}
    assert filters["repository.private"]["equals"] is False
    assert route["script"] == "github-route-same-repo.py"
    assert route["skills"] == ["dryvist-pr-review"]
    assert route["toolsets"] == ["terminal", "web"]
    assert "profile" not in route


def test_config_has_no_memory_slack_or_mcp():
    cfg = _config()
    assert cfg["model"]["default"] == "review-model"
    assert cfg["model"]["base_url"] == "http://router.invalid/v1"
    assert cfg["model"]["api_key"] == "${HERMES_AGENT_MODEL_API_KEY}"
    assert cfg["memory"] == {"memory_enabled": False, "user_profile_enabled": False}
    assert cfg["platform_toolsets"] == {"webhook": ["terminal", "web"]}
    assert "mcp_servers" not in cfg
    assert "slack" not in cfg["platforms"]
    text = (ROLE / "templates" / "config-public-gateway.yaml.j2").read_text().lower()
    assert "hindsight" not in text and "slack" not in text


def test_env_carries_only_the_secret_and_model_key():
    text = _env().get_template("hermes-env-public-gateway.j2").render(**_ctx())
    keys = {line.split("=", 1)[0] for line in text.splitlines() if line and not line.startswith("#") and "=" in line}
    secrets = {k for k in keys if any(w in k for w in ("TOKEN", "KEY", "SECRET", "PASSWORD"))}
    assert secrets == {"HERMES_AGENT_MODEL_API_KEY", "WEBHOOK_SECRET"}
    assert "HERMES_AGENT_MODEL_API_KEY=router-key" in text
    assert "WEBHOOK_PORT=8645" in text
    assert not any(k.startswith(("SLACK", "SPLUNK", "ZAMMAD", "GH_", "HINDSIGHT")) for k in keys)


def test_unit_pins_the_public_boundary_and_its_own_home():
    unit = _env().get_template("hermes-gateway-public.service.j2").render(**_ctx())
    assert "Environment=HERMES_TRUST_BOUNDARY=public" in unit
    assert f"Environment=HERMES_HOME={HOME}" in unit
    assert f"WorkingDirectory={HOME}" in unit
    assert f"EnvironmentFile={HOME}/.env" in unit
    assert "HERMES_GH_TOKEN_SET" not in unit
    assert "--replace" not in unit
    assert "OnFailure=hermes-unit-alert@%n.service" in unit


def test_tasks_are_gated_on_the_public_route():
    tasks = yaml.safe_load((ROLE / "tasks" / "public_gateway.yml").read_text())
    on = "hermes_agent_github_route_public_enabled | bool"
    off = "not (hermes_agent_github_route_public_enabled | bool)"
    for t in tasks:
        gate = t["when"] if isinstance(t["when"], list) else [t["when"]]
        assert (on in gate) != (off in gate), t["name"]
    start = _by_name(tasks, "Enable and start the public-review gateway")["ansible.builtin.systemd"]
    stop = _by_name(tasks, "Stop and disable the retained public-review gateway")["ansible.builtin.systemd"]
    assert (start["state"], start["enabled"]) == ("started", True)
    assert (stop["state"], stop["enabled"]) == ("stopped", False)
    env_task = _by_name(tasks, "Deploy the public-review gateway secrets (.env)")
    assert env_task["ansible.builtin.template"]["mode"] == "0600" and env_task["no_log"] is True
    skill = _by_name(tasks, "Deploy the review skill for the public-review gateway")["ansible.builtin.copy"]
    assert skill["src"].endswith("/skills/dryvist/{{ hermes_agent_github_public_gateway_skill }}/")
    assert DEFAULTS["hermes_agent_github_public_gateway_skill"] == "pr-review"


def test_converge_wiring_and_model_assert():
    flat = role_tasks(ROLE)
    assert any(t.get("name") == "Deploy the public-review gateway systemd unit" for t in flat)
    check = _by_name(flat, "Assert the public github gateway inputs")
    assert "hermes_agent_github_public_gateway_model | length > 0" in check["ansible.builtin.assert"]["that"]
    assert check["when"] == "hermes_agent_github_route_public_enabled | bool"
    handlers = yaml.safe_load((ROLE / "handlers" / "main.yml").read_text())
    assert any(h["name"] == "Restart hermes-gateway-public" for h in handlers)


def test_default_gateway_unit_keeps_its_own_boundary():
    unit = template_text(ROLE, "hermes-gateway.service.j2")
    assert "HERMES_TRUST_BOUNDARY={{ hermes_agent_github_trust_boundary_default }}" in unit


def test_relay_env_points_public_events_at_the_public_gateway_port():
    text = _env().get_template("hermes-event-relay.env.j2").render(
        ansible_managed="managed",
        hermes_agent_event_relay_target_base="http://127.0.0.1:8644/webhooks",
        hermes_agent_event_relay_target_base_public="http://127.0.0.1:8645/webhooks",
        hermes_agent_event_relay_queue_service="q",
        hermes_agent_event_relay_queue_url="u",
        hermes_agent_event_relay_dlq_url="d",
        hermes_agent_event_relay_queue_region="r",
        hermes_agent_event_relay_bao_addr="a",
        hermes_agent_event_relay_bao_role_id="i",
        hermes_agent_event_relay_bao_secret_id="s",
        hermes_agent_event_relay_bao_creds_path="p",
        hermes_agent_webhook_secret="w",
        hermes_agent_event_relay_route_public="github-public",
        hermes_agent_event_relay_route_private="github-private",
        hermes_agent_event_relay_healthcheck_url="",
        hermes_agent_event_relay_stats_seconds=300,
    )
    assert "RELAY_TARGET_BASE=http://127.0.0.1:8644/webhooks\n" in text
    assert "RELAY_TARGET_BASE_PUBLIC=http://127.0.0.1:8645/webhooks\n" in text
    assert DEFAULTS["hermes_agent_event_relay_target_base_public"].strip().endswith(
        "{{ hermes_agent_github_public_gateway_port }}/webhooks"
    )
