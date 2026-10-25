"""Render-contract tests for opt-in Hermes native A2A support."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml
from jinja2 import Environment

from _role_files import role_defaults


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
CONFIG_TEMPLATE = (ROLE_ROOT / "templates" / "config.yaml.j2").read_text()
ENV_TEMPLATE = (ROLE_ROOT / "templates" / "hermes-env.j2").read_text()
LEGACY_SLACK_CONFIG = """platforms:
  # Slack delivery behavior: replies and cron continuations go in threads (see
  # hermes_agent_slack_reply_in_thread for why the two knobs move together).
  # These are bridged from platforms.<p> by gateway/config.py; Slack enablement
  # still comes from the SLACK_* tokens in .env, so this block only tunes behavior.
  slack:
    reply_in_thread: true
    cron_continuable_surface: thread
"""


def _environment() -> Environment:
    environment = Environment(autoescape=False, trim_blocks=True)
    environment.filters["to_json"] = json.dumps
    environment.filters["bool"] = bool
    environment.filters["regex_replace"] = (
        lambda value, pattern, replacement: re.sub(pattern, replacement, str(value))
    )
    return environment


def _template_fragment(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end, source.index(start))]


def _render_config_fragment(source: str, start: str, end: str, **context: object) -> str:
    return _environment().from_string(_template_fragment(source, start, end)).render(**context)


def _gateway_fragment(**context: object) -> str:
    return _render_config_fragment(
        CONFIG_TEMPLATE,
        "{% set _api_server_on",
        "# H-17 per-platform",
        **context,
    )


def _peer_fragment(**context: object) -> str:
    return _render_config_fragment(
        CONFIG_TEMPLATE,
        "{% if hermes_agent_a2a_peers | length > 0 %}\na2a_agents:",
        "{% set _slack_on",
        **context,
    )


def _toolsets_fragment(**context: object) -> str:
    return _render_config_fragment(
        CONFIG_TEMPLATE,
        "platform_toolsets:",
        "{% if hermes_agent_a2a_peers | length > 0 %}\na2a_agents:",
        **context,
    )


def _slack_fragment(**context: object) -> str:
    return _render_config_fragment(
        CONFIG_TEMPLATE,
        "platforms:\n{% if _slack_on %}",
        "{% include 'config-webhook.yaml.j2' %}",
        **context,
    )


def test_default_slack_config_routes_reactions_on_bot_posts() -> None:
    defaults = role_defaults(ROLE_ROOT)
    slack = yaml.safe_load(
        _slack_fragment(
            _slack_on=True,
            hermes_agent_slack_rich_blocks=defaults["hermes_agent_slack_rich_blocks"],
            hermes_agent_slack_reaction_triggers=defaults["hermes_agent_slack_reaction_triggers"],
            hermes_agent_slack_reply_in_thread=True,
            hermes_agent_slack_cron_continuable_surface="thread",
        )
    )["platforms"]["slack"]
    assert slack["extra"] == {"reaction_triggers": True}


A2A_ENABLED_DEFAULT = (
    "{{ hermes_agent_id == 'hermes' or hermes_agent_a2a_inbound_token | length > 0 }}"
)


def test_explicitly_disabling_a2a_keeps_existing_gateway_and_slack_config() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert defaults["hermes_agent_a2a_inbound_enabled"] == A2A_ENABLED_DEFAULT
    assert defaults["hermes_agent_a2a_peers"] == []
    assert defaults["hermes_agent_slack_rich_blocks"] is False

    # Explicitly disabling inbound A2A preserves the old gateway render shapes.
    for identity in ("hermes", "hermes-private", "donna"):
        assert _gateway_fragment(
            hermes_agent_api_server_key="",
            hermes_agent_a2a_inbound_enabled=False,
            hermes_agent_a2a_inbound_port=9901,
            hermes_agent_api_max_concurrent_runs=8,
            hermes_agent_id=identity,
        ) == ""
        assert _gateway_fragment(
            hermes_agent_api_server_key="fixture-api-key",
            hermes_agent_a2a_inbound_enabled=False,
            hermes_agent_a2a_inbound_port=9901,
            hermes_agent_api_max_concurrent_runs=8,
            hermes_agent_id=identity,
        ) == (
            "# Inbound job-submission API queue hardening. The platform itself is enabled by\n"
            "# key presence via the .env bridge (API_SERVER_* in hermes-env.j2); this caps\n"
            "# in-flight agent runs across all its agent-serving endpoints. Over-cap\n"
            "# submissions are refused up front (429 + Retry-After) instead of stacking\n"
            "# prefills on the single shared serving deployment — see defaults/main.yml\n"
            "# for the measured-envelope rationale behind the value.\n"
            "gateway:\n  api_server:\n    max_concurrent_runs: 8\n"
        )
        assert _peer_fragment(
            hermes_agent_a2a_peers=[], hermes_agent_env_prefix=identity.upper()
        ) == ""

        slack_config = _slack_fragment(
            _slack_on=True,
            hermes_agent_slack_rich_blocks=False,
            hermes_agent_slack_reaction_triggers=False,
            hermes_agent_slack_reply_in_thread=True,
            hermes_agent_slack_cron_continuable_surface="thread",
        )
        assert slack_config == LEGACY_SLACK_CONFIG

    # Slack inherits the base Cron list, which no longer includes Kanban.
    toolsets = defaults["hermes_agent_slack_toolsets"]
    rendered_toolsets = _environment().from_string(str(toolsets)).render(
        hermes_agent_cron_toolsets=defaults["hermes_agent_api_server_toolsets"],
        hermes_agent_a2a_peers=[],
    )
    expected_slack_toolsets = defaults["hermes_agent_api_server_toolsets"] + ["clarify"]
    assert yaml.safe_load(rendered_toolsets) == expected_slack_toolsets
    policy = yaml.safe_load(_toolsets_fragment(
        hermes_agent_cron_toolsets=defaults["hermes_agent_api_server_toolsets"],
        hermes_agent_api_server_key="",
        hermes_agent_api_server_toolsets=defaults["hermes_agent_api_server_toolsets"],
        hermes_agent_slack_bot_token="fixture-slack-token",
        hermes_agent_slack_toolsets=expected_slack_toolsets,
        hermes_agent_a2a_peers=[],
    ))
    assert policy["platform_toolsets"]["slack"] == expected_slack_toolsets
    assert "kanban" not in policy["platform_toolsets"]["cron"]
    assert "kanban" not in policy["platform_toolsets"]["slack"]


def test_enabled_a2a_renders_authenticated_inbound_peer_and_slack_config() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert defaults["hermes_agent_a2a_inbound_enabled"] == A2A_ENABLED_DEFAULT
    peers = [{
        "name": "specialist",
        "url": "https://specialist.example.invalid/a2a",
        "token": "fixture-outbound-token",
    }]
    context = {
        "hermes_agent_a2a_inbound_enabled": defaults["hermes_agent_a2a_inbound_enabled"],
        "hermes_agent_a2a_inbound_port": 9901,
        "hermes_agent_a2a_peers": peers,
        "hermes_agent_env_prefix": "HERMES",
        "hermes_agent_slack_rich_blocks": True,
    }

    gateway = yaml.safe_load(_gateway_fragment(
        **context,
        hermes_agent_api_server_key="",
        hermes_agent_api_max_concurrent_runs=8,
    ))
    assert gateway == {
        "gateway": {
            "platforms": {"a2a": {"enabled": True, "extra": {"port": 9901}}}
        }
    }

    a2a_agents = yaml.safe_load(_peer_fragment(**context))
    assert a2a_agents == {
        "a2a_agents": {
            "specialist": {
                "url": "https://specialist.example.invalid/a2a",
                "auth": {
                    "type": "bearer",
                    "token": "${HERMES_A2A_PEER_0_TOKEN}",
                },
            }
        }
    }
    assert "fixture-outbound-token" not in _peer_fragment(**context)

    defaults = role_defaults(ROLE_ROOT)
    slack_toolsets_expression = _environment().from_string(
        str(defaults["hermes_agent_slack_toolsets"])
    ).render(
        hermes_agent_cron_toolsets=defaults["hermes_agent_api_server_toolsets"],
        hermes_agent_a2a_peers=peers,
    )
    slack_toolsets = yaml.safe_load(slack_toolsets_expression)
    assert "a2a" in slack_toolsets
    policy = yaml.safe_load(_toolsets_fragment(
        hermes_agent_cron_toolsets=defaults["hermes_agent_api_server_toolsets"],
        hermes_agent_api_server_key="",
        hermes_agent_api_server_toolsets=defaults["hermes_agent_api_server_toolsets"],
        hermes_agent_slack_bot_token="fixture-slack-token",
        hermes_agent_slack_toolsets=slack_toolsets,
        hermes_agent_a2a_peers=peers,
    ))
    assert "a2a" in policy["platform_toolsets"]["slack"]

    slack = yaml.safe_load(_slack_fragment(
        _slack_on=True,
        hermes_agent_slack_rich_blocks=True,
        hermes_agent_slack_reply_in_thread=True,
        hermes_agent_slack_cron_continuable_surface="thread",
    ))
    assert slack["platforms"]["slack"]["extra"]["rich_blocks"] is True

    env_start = "{% if hermes_agent_a2a_inbound_enabled | bool %}\n# Inbound A2A"
    env_end = "# The 4-channel routing ids"
    env_fragment = _template_fragment(ENV_TEMPLATE, env_start, env_end)
    rendered_env = _environment().from_string(env_fragment).render(
        **context,
        hermes_agent_a2a_inbound_host="0.0.0.0",
        hermes_agent_a2a_inbound_token="fixture-inbound-token",
    )
    assert "A2A_BEARER_TOKEN=fixture-inbound-token" in rendered_env
    assert "A2A_HOST=0.0.0.0" in rendered_env
    assert "HERMES_A2A_PEER_0_TOKEN=fixture-outbound-token" in rendered_env


def test_enabled_inbound_without_token_fails_the_role_assert() -> None:
    assert shutil.which("ansible-playbook"), "run this test inside the repository dev shell"
    task_path = ROLE_ROOT / "tasks" / "assert_a2a.yml"
    playbook = [{
        "name": "A2A inbound token negative case",
        "hosts": "localhost",
        "gather_facts": False,
        "connection": "local",
        "vars": {
            "hermes_agent_a2a_inbound_enabled": True,
            "hermes_agent_a2a_inbound_token": "",
            "hermes_agent_env_prefix": "HERMES",
            "hermes_agent_id": "hermes",
        },
        "tasks": [{
            "name": "Run the role's A2A assertion tasks",
            "ansible.builtin.import_tasks": str(task_path),
        }],
    }]

    with tempfile.TemporaryDirectory(prefix="hermes-a2a-test-") as temp_dir:
        path = Path(temp_dir) / "negative.yml"
        path.write_text(yaml.safe_dump(playbook, sort_keys=False))
        result = subprocess.run(
            ["ansible-playbook", "--inventory", "localhost,", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "inbound token is empty" in output
    assert "Set HERMES_A2A_TOKEN" in output
    assert "fixture-inbound-token" not in output
