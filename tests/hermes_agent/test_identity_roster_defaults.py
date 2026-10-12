"""Roster identities project their identity and Slack channels into role defaults."""

from __future__ import annotations

import re

from jinja2 import Environment
import yaml

from _role_files import role_defaults
from conftest import ROLE_ROOT


_DEFAULT_KEYS = (
    "hermes_agent_id",
    "hermes_agent_env_prefix",
    "hermes_agent_display_name",
    "hermes_agent_user",
    "hermes_agent_home",
    "hermes_agent_slack_home_channel",
)


def _render_identity_defaults(**overrides: object) -> dict[str, str]:
    defaults = role_defaults(ROLE_ROOT)
    context: dict[str, object] = {"hermes_agent_identity": "hermes"}
    context.update(overrides)
    env = Environment(autoescape=False)
    env.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(
        pattern, replacement, value
    )
    env.globals["lookup"] = lambda _plugin, _name: ""

    rendered: dict[str, str] = {}
    for key in _DEFAULT_KEYS:
        rendered[key] = env.from_string(str(defaults[key])).render(
            **context, **rendered
        )
    return rendered


def test_legacy_identity_keeps_existing_user_home_and_display() -> None:
    rendered = _render_identity_defaults()

    assert rendered["hermes_agent_user"] == "hermes"
    assert rendered["hermes_agent_home"] == "/var/lib/hermes"
    assert rendered["hermes_agent_display_name"] == "Hermes"


def test_roster_identity_projects_display_user_home_and_home_channel() -> None:
    rendered = _render_identity_defaults(
        hermes_agent_identity="splunk-heimdall",
        ai_agents={
            "splunk-heimdall": {
                "display": "Heimdall",
                "slack": {
                    "home_channel": "example-home-channel",
                    "requests_channel": "example-requests-channel",
                },
            }
        },
    )

    assert rendered["hermes_agent_user"] == "splunk-heimdall"
    assert rendered["hermes_agent_home"] == "/var/lib/splunk-heimdall"
    assert rendered["hermes_agent_display_name"] == "Heimdall"
    assert rendered["hermes_agent_slack_home_channel"] == "example-home-channel"


def _evaluate(expression: str, **context: object) -> bool:
    rendered = Environment(autoescape=False).from_string(
        "{{ " + expression + " }}"
    ).render(**context)
    assert rendered in ("True", "False"), rendered
    return rendered == "True"


def test_missing_roster_user_is_tolerated_only_in_check_mode() -> None:
    tasks = yaml.safe_load(
        (ROLE_ROOT / "tasks" / "venv_extras_and_users.yml").read_text()
    )
    lookup = next(
        task
        for task in tasks
        if task.get("name", "").startswith("Look up the hermes user's uid")
    )
    assert lookup["register"] == "hermes_agent_user_getent"
    assert lookup["changed_when"] is False
    assert not _evaluate(
        lookup["failed_when"],
        hermes_agent_user_getent={"failed": True},
        ansible_check_mode=True,
    )
    assert _evaluate(
        lookup["failed_when"],
        hermes_agent_user_getent={"failed": True},
        ansible_check_mode=False,
    )

    set_uid = next(
        task for task in tasks if task.get("name") == "Set hermes_agent_uid fact"
    )
    uid_expression = set_uid["ansible.builtin.set_fact"]["hermes_agent_uid"]
    assert Environment(autoescape=False).from_string(uid_expression).render(
        hermes_agent_user="splunk-heimdall", getent_passwd={}
    ) == "0"


def test_roster_home_must_be_a_mount_and_legacy_assertion_is_skipped() -> None:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "assert.yml").read_text())
    gather_mounts = next(
        task
        for task in tasks
        if task.get("name") == "Gather mount facts for the roster agent home assertion"
    )
    mount_assert = next(
        task
        for task in tasks
        if task.get("name") == "Assert the roster agent home is a dedicated mount point"
    )
    assert gather_mounts["ansible.builtin.setup"]["gather_subset"] == ["mounts"]
    assert "ansible_mounts is not defined" in gather_mounts["when"]
    condition = mount_assert["ansible.builtin.assert"]["that"][0]
    assert _evaluate(
        condition,
        ansible_mounts=[{"mount": "/"}, {"mount": "/var/lib/splunk-heimdall"}],
        hermes_agent_home="/var/lib/splunk-heimdall",
    )
    assert not _evaluate(
        condition,
        ansible_mounts=[{"mount": "/"}],
        hermes_agent_home="/var/lib/splunk-heimdall",
    )
    assert not _evaluate(
        mount_assert["when"], hermes_agent_id="hermes", ai_agents={}
    )
