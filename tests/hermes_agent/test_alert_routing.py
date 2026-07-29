"""CRITICAL anomaly alerts must reach #hermes-all, not only the operator DM.

#hermes-all is the complete log of record. An anomaly that exists only in a DM
is invisible to anyone reading that channel and cannot be correlated against the
digests around it.

Upstream's cron scheduler splits ``deliver`` on commas and dedups the resolved
targets by (platform, chat_id, thread_id) — see ``_normalize_deliver_value`` and
``_resolve_delivery_targets`` in ``cron/scheduler.py``. These tests pin the
rendered value, and model that same split/dedup so the assertions describe
delivery outcomes rather than string shape.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles" / "hermes_agent" / "defaults" / "main.yml").read_text()
)

_ENV = Environment(autoescape=False)


def _render(template: str, **context: str) -> str:
    return _ENV.from_string(template).render(**context).strip()


def _alert_targets(*, allowed_users: str, hermes_all: str) -> list[str]:
    """Resolve the alert job's deliver value the way the scheduler does."""
    suffix = _render(
        DEFAULTS["hermes_agent_hermes_all_deliver_suffix"],
        hermes_agent_slack_hermes_all_channel=hermes_all,
    )
    deliver = _render(
        DEFAULTS["hermes_agent_splunk_alert_deliver"],
        hermes_agent_slack_allowed_users=allowed_users,
        hermes_agent_hermes_all_deliver_suffix=suffix,
    )
    seen: list[str] = []
    for part in (p.strip() for p in deliver.split(",")):
        if part and part not in seen:
            seen.append(part)
    return seen


def test_critical_alert_reaches_both_the_dm_and_hermes_all() -> None:
    targets = _alert_targets(allowed_users="U123", hermes_all="C_ALL")
    assert targets == ["slack:U123", "slack:C_ALL"]


def test_alert_still_reaches_hermes_all_without_an_operator_member_id() -> None:
    # No member id → the DM leg degrades to the home channel, but the log of
    # record must still get the post.
    targets = _alert_targets(allowed_users="", hermes_all="C_ALL")
    assert "slack:C_ALL" in targets


def test_no_trailing_empty_target_when_hermes_all_is_unset() -> None:
    # A bare trailing comma would resolve to nothing and could mask a
    # misconfiguration as "delivered".
    targets = _alert_targets(allowed_users="U123", hermes_all="")
    assert targets == ["slack:U123"]


def test_duplicate_collapses_while_hermes_all_defaults_to_the_firehose() -> None:
    # hermes_all currently defaults to the firehose channel id, so the DM leg
    # and the log leg can resolve to the same channel. Upstream dedups by
    # (platform, chat_id, thread_id), so this must not double-post.
    targets = _alert_targets(allowed_users="C_FIRE", hermes_all="C_FIRE")
    assert targets == ["slack:C_FIRE"]


def test_suffix_is_used_rather_than_a_second_hardcoded_channel() -> None:
    # The channel must never be inlined into the alert var — that is how the
    # two definitions drift apart.
    assert (
        "hermes_agent_hermes_all_deliver_suffix"
        in DEFAULTS["hermes_agent_splunk_alert_deliver"]
    )
