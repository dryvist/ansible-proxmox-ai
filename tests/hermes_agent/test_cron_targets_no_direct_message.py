"""No scheduled Hermes job delivers to a Slack direct message.

A Slack target whose ID is a DM conversation (D...) or a user (U..., W...)
opens a direct message. Every cron deliver and failure-deliver target passes
through one replacement at plan time, so such an entry becomes the shared
status channel, or local when none is set. These tests fail when a job
declares a DM target or when a reconcile path skips the replacement.
"""

from __future__ import annotations

import re

import yaml
from jinja2 import Environment

from _role_files import role_defaults
from test_a2a_config import ROLE_ROOT

DM_TARGET = re.compile(r"slack:[DUW][A-Z0-9]+")


def _replace(target: str, status_channel: str) -> str:
    defaults = role_defaults(ROLE_ROOT)
    replacement = Environment().from_string(defaults["hermes_agent_cron_dm_replacement"]).render(
        hermes_agent_slack_status_channel=status_channel
    ).strip()
    return re.sub(defaults["hermes_agent_cron_dm_pattern"], replacement, target)


def test_dm_and_user_targets_move_to_the_status_channel() -> None:
    assert _replace("slack:D0EXAMPLE1", "C0STATUS1") == "slack:C0STATUS1"
    assert _replace("slack:U0EXAMPLE1", "C0STATUS1") == "slack:C0STATUS1"
    assert _replace("slack:W0EXAMPLE1:1700000000.1", "C0STATUS1") == "slack:C0STATUS1"
    assert _replace("local,slack:D0EXAMPLE1", "C0STATUS1") == "local,slack:C0STATUS1"
    assert _replace("slack:D0EXAMPLE1, slack:C0OTHER01", "C0STATUS1") == "slack:C0STATUS1, slack:C0OTHER01"


def test_dm_targets_stay_local_without_a_status_channel() -> None:
    assert _replace("slack:D0EXAMPLE1", "") == "local"


def test_channel_and_literal_targets_are_unchanged() -> None:
    for target in ("", "local", "log", "slack:C0EXAMPLE1", "slack:G0EXAMPLE1", "slack:C0A,local"):
        assert _replace(target, "C0STATUS1") == target


def _declared_targets() -> list[tuple[str, str]]:
    found = []
    for path in sorted((ROLE_ROOT / "defaults" / "main").glob("*.yml")):
        for key, value in (yaml.safe_load(path.read_text()) or {}).items():
            if key.startswith("hermes_agent_direct_cron_jobs_") and isinstance(value, list):
                for job in value:
                    for field in ("deliver", "failure_deliver", "channel_when_healthy"):
                        if field in job:
                            found.append((str(job.get("name")), str(job[field])))
    assert found
    return found


def test_no_declared_job_names_a_dm_target() -> None:
    for name, target in _declared_targets():
        assert not DM_TARGET.search(target), f"{name} delivers to a direct message: {target}"


def test_every_reconcile_path_applies_the_dm_replacement() -> None:
    applied = "regex_replace(hermes_agent_cron_dm_pattern, hermes_agent_cron_dm_replacement)"
    plan = (ROLE_ROOT / "tasks" / "reconcile_direct_cron_plan.yml").read_text()
    task = next(t for t in yaml.safe_load(plan) if t["name"] == "Direct cron | Plan every job")
    assert applied in task["vars"]["hermes_agent_direct_deliver"]
    assert applied in task["vars"]["hermes_agent_direct_failure_deliver"]
    enqueuer = (ROLE_ROOT / "tasks" / "reconcile_enqueuer_cron.yml").read_text()
    assert f"item.deliver | default('local')\n         | {applied}" in enqueuer
    assert applied in role_defaults(ROLE_ROOT)["hermes_agent_cron_failure_deliver"]
