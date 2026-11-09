"""Credential canary checks for named-profile env files and Slack routes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template
from jinja2 import Environment, StrictUndefined

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
SCRIPT_TEMPLATE = ROLE / "templates/hermes-credential-canary.py.j2"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main/197-credential-canary.yml").read_text())
FIELDS = [
    "HERMES_AGENT_MODEL_API_KEY",
    "ZAMMAD_API_TOKEN",
    "HASS_TOKEN",
    "GH_PAT_WRITE_PROJECT_ISSUES",
    "HERMES_LANGFUSE_PUBLIC_KEY",
    "HERMES_LANGFUSE_SECRET_KEY",
    "FIRECRAWL_API_KEY",
    "A2A_TEST_PROFILE_A2A_PEER_0_TOKEN",
]
FIELD_VALUES = {field: f"secret-value-{index}" for index, field in enumerate(FIELDS)}
PROFILE = "homelab-admin"


def _render_fields() -> list[dict]:
    variables = {
        "hermes_agent_profiles": [
            {"name": PROFILE, "env": ["zammad", "homeassistant"], "mcp": ["docs"]},
            {"name": "splunk-admin", "env": ["splunk"], "mcp": ["splunk"]},
            {"name": "github-maint", "env": ["github"], "mcp": ["docs"]},
            {"name": "eval", "env": ["api_server"], "mcp": []},
        ],
        "hermes_agent_splunk_access": True,
        "hermes_agent_homeassistant_enabled": True,
        "hermes_agent_owned_mcp_slugs": ["splunk"],
        "hermes_agent_langfuse_enabled": True,
        "hermes_agent_firecrawl_enabled": True,
        "hermes_agent_a2a_delegation_enabled": True,
        "hermes_agent_a2a_delegating_profiles": [PROFILE],
        "hermes_agent_a2a_authorized_peers": [{"token": "must-not-render"}],
        "hermes_agent_env_prefix": "A2A_TEST",
        "hermes_agent_credential_canary_profile": PROFILE,
        "hermes_agent_slack_profile_channels": {
            "splunk-admin": "C-TEST-SPLUNK",
            "github-maint": "C-TEST-GITHUB",
        },
    }
    templar = Templar(loader=DataLoader())
    templar.available_variables = variables
    rendered = templar.template(
        trust_as_template(DEFAULTS["hermes_agent_credential_canary_profile_fields"])
    )
    return json.loads(rendered)


def _render_script(tmp_path: Path, fields: list[str] | None = None) -> Path:
    environment = Environment(undefined=StrictUndefined, autoescape=False)
    environment.filters["to_json"] = json.dumps
    expectations = [{"name": PROFILE, "fields": fields or FIELDS}]
    rendered = environment.from_string(SCRIPT_TEMPLATE.read_text()).render(
        hermes_agent_hermes_home=str(tmp_path),
        hermes_agent_credential_canary_profile_fields=json.dumps(expectations),
    )
    path = tmp_path / "credential-canary.py"
    path.write_text(rendered)
    return path


def _write_profile_env(root: Path, *, omit: set[str] | None = None) -> None:
    profile_home = root / "profiles" / PROFILE
    profile_home.mkdir(parents=True, exist_ok=True)
    lines = [f"{field}={value}" for field, value in FIELD_VALUES.items() if field not in (omit or set())]
    (profile_home / ".env").write_text("\n".join(lines) + "\n")


def _write_gateway_config(root: Path, profiles: tuple[str, ...] = (PROFILE,)) -> None:
    routes = "\n".join(
        f'    - name: "{name}-reports"\n      platform: slack\n      chat_id: "C-TEST"\n      profile: "{name}"'
        for name in profiles
    )
    (root / "config.yaml").write_text(f"gateway:\n  profile_routes:\n{routes}\n")


def _run_canary(root: Path, *, omit_field: str | None = None,
                routes: tuple[str, ...] = (PROFILE,)) -> subprocess.CompletedProcess:
    _write_profile_env(root, omit={omit_field} if omit_field else None)
    _write_gateway_config(root, profiles=routes)
    script = _render_script(root)
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=False)
    output = result.stdout + result.stderr
    assert all(value not in output for value in FIELD_VALUES.values())
    return result


def test_profile_field_expectations_follow_the_environment_template_gates() -> None:
    rendered_profiles = _render_fields()
    profiles = {profile["name"]: profile["fields"] for profile in rendered_profiles}
    route_requirements = {
        profile["name"]: profile["route_required"] for profile in rendered_profiles
    }
    assert profiles[PROFILE] == [
        "HERMES_AGENT_MODEL_API_KEY",
        "ZAMMAD_API_TOKEN",
        "HASS_TOKEN",
        "HERMES_LANGFUSE_PUBLIC_KEY",
        "HERMES_LANGFUSE_SECRET_KEY",
        "FIRECRAWL_API_KEY",
        "A2A_TEST_PROFILE_A2A_PEER_0_TOKEN",
    ]
    assert "SPLUNK_MCP_TOKEN" in profiles["splunk-admin"]
    assert "HERMES_AGENT_GATEWAY_KEY" in profiles["splunk-admin"]
    assert "GH_PAT_WRITE_PROJECT_ISSUES" in profiles["github-maint"]
    assert "API_SERVER_KEY" in profiles["eval"]
    assert route_requirements == {
        PROFILE: True,
        "splunk-admin": True,
        "github-maint": True,
        "eval": False,
    }
    assert all("SLACK_" not in field for profile in profiles.values() for field in profile)
    assert "must-not-render" not in json.dumps(profiles)


def test_canary_passes_when_profile_fields_and_primary_route_exist(tmp_path: Path) -> None:
    result = _run_canary(tmp_path)
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report == {"ok": True, "detail": "all named profile credentials and Slack routes are present"}


def test_canary_names_a_missing_profile_field_without_printing_its_value(tmp_path: Path) -> None:
    result = _run_canary(tmp_path, omit_field="ZAMMAD_API_TOKEN")
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["ok"] is False
    assert "homelab-admin.ZAMMAD_API_TOKEN" in report["detail"]


def test_canary_names_a_missing_profile_route(tmp_path: Path) -> None:
    result = _run_canary(tmp_path, routes=("splunk-admin",))
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["ok"] is False
    assert "homelab-admin.profile_routes" in report["detail"]


def test_direct_cron_can_create_no_agent_script_jobs_without_changing_agentic_jobs() -> None:
    tasks = yaml.safe_load((ROLE / "tasks/reconcile_direct_cron.yml").read_text())
    build = next(task for task in tasks if task.get("name") == "Direct cron | Build create argv for {{ item.name }}")
    expression = build["ansible.builtin.set_fact"]["hermes_agent_direct_argv"]

    def argv_for(item: dict, prompt: str, model: str) -> list[str]:
        templar = Templar(loader=DataLoader())
        templar.available_variables = {
            "hermes_agent_bin": "/usr/local/bin/hermes",
            "hermes_agent_cli_profile_args": [],
            "item": item,
            "hermes_agent_direct_no_agent": item.get("no_agent", False),
            "hermes_agent_direct_prompt": prompt,
            "hermes_agent_direct_model": model,
            "hermes_agent_direct_model_provider": "custom",
            "hermes_agent_direct_deliver": "slack:test",
            "hermes_agent_direct_failure_deliver": "",
            "hermes_agent_hermes_home": "/srv/hermes-home",
        }
        return templar.template(trust_as_template(expression))

    script_job = argv_for(
        {"name": "credential-canary-c6", "schedule": "17 */6 * * *", "script": "canary.py",
         "no_agent": True, "hermes_home": "/srv/hermes-home/profiles/homelab-admin"},
        prompt="",
        model="",
    )
    assert script_job == [
        "/usr/local/bin/hermes", "cron", "create", "17 */6 * * *", "--name", "credential-canary-c6",
        "--script", "canary.py", "--no-agent", "--deliver", "slack:test", "--workdir",
        "/srv/hermes-home/profiles/homelab-admin",
    ]

    agentic_job = argv_for(
        {"name": "existing-job", "schedule": "0 7 * * *", "script": "monitor.py",
         "hermes_home": "/srv/hermes-home"},
        prompt="existing prompt",
        model="test-model",
    )
    assert "--no-agent" not in agentic_job
    assert "existing prompt" in agentic_job
    assert "--model" in agentic_job


# develop's checksum expression before `no_agent` existed: the desired-state
# markers of every existing direct cron were written with it.
DEVELOP_CHECKSUM = (
    "{{ ({'schedule': item.schedule, 'prompt': hermes_agent_direct_prompt,"
    " 'skill': item.skill | default(''), 'script': item.script | default(''),"
    " 'deliver': hermes_agent_direct_deliver,"
    " 'workdir': item.hermes_home | default(hermes_agent_hermes_home),"
    " 'failure_deliver': hermes_agent_direct_failure_deliver}"
    " | combine({'model': hermes_agent_direct_model} if hermes_agent_direct_model | length > 0 else {})"
    " | combine({'model_provider': hermes_agent_direct_model_provider}"
    " if hermes_agent_direct_model | length > 0 else {})"
    " | combine({'enabled_toolsets': item.enabled_toolsets} if item.enabled_toolsets is defined else {}))"
    " | to_json | hash('sha256') }}"
)


def test_existing_direct_cron_checksums_stay_stable() -> None:
    tasks = yaml.safe_load((ROLE / "tasks/reconcile_direct_cron.yml").read_text())
    calc = next(task for task in tasks
                if task.get("name") == "Direct cron | Calculate desired state for {{ item.name }}")
    expression = calc["ansible.builtin.set_fact"]["hermes_agent_direct_checksum"]

    def checksum(template: str, item: dict) -> str:
        templar = Templar(loader=DataLoader())
        templar.available_variables = {
            "item": item,
            "hermes_agent_direct_no_agent": item.get("no_agent", False),
            "hermes_agent_direct_prompt": "existing prompt",
            "hermes_agent_direct_model": "test-model",
            "hermes_agent_direct_model_provider": "custom",
            "hermes_agent_direct_deliver": "slack:test",
            "hermes_agent_direct_failure_deliver": "",
            "hermes_agent_hermes_home": "/srv/hermes-home",
        }
        return templar.template(trust_as_template(template))

    agentic = {"name": "existing-job", "schedule": "0 7 * * *", "script": "monitor.py"}
    assert checksum(expression, agentic) == checksum(DEVELOP_CHECKSUM, agentic)
    script_only = {**agentic, "no_agent": True}
    assert checksum(expression, script_only) != checksum(DEVELOP_CHECKSUM, script_only)
