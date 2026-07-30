"""Everything that must reach #hermes-all actually resolves there.

#hermes-all is the complete log of record. An anomaly that exists only in a DM
is invisible to anyone reading that channel and cannot be correlated against the
digests around it — and a firehose job that only resolves to the firehose leaves
#hermes-all empty the moment the two channels stop being the same id.

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


def _firehose_targets(*, firehose: str, hermes_all: str) -> list[str]:
    """Resolve the firehose deliver value the way the scheduler does."""
    suffix = _render(
        DEFAULTS["hermes_agent_hermes_all_deliver_suffix"],
        hermes_agent_slack_hermes_all_channel=hermes_all,
    )
    deliver = _render(
        DEFAULTS["hermes_agent_firehose_deliver"],
        hermes_agent_slack_firehose_channel=firehose,
        hermes_agent_hermes_all_deliver_suffix=suffix,
    )
    seen: list[str] = []
    for part in (p.strip() for p in deliver.split(",")):
        if part and part not in seen:
            seen.append(part)
    return seen


def test_firehose_jobs_still_reach_hermes_all_once_the_channels_diverge() -> None:
    # The regression this guards: the defaults tell the operator to override
    # hermes_all "once one is created". Doing exactly that used to leave
    # #hermes-all receiving nothing from any firehose job.
    assert _firehose_targets(firehose="C_FIRE", hermes_all="C_ALL") == [
        "slack:C_FIRE",
        "slack:C_ALL",
    ]


def test_firehose_fanout_is_a_noop_while_the_channels_are_the_same() -> None:
    # hermes_all defaults to the firehose id, so both legs resolve identically
    # and upstream's dedup collapses them. Today this must not double-post.
    assert _firehose_targets(firehose="C_FIRE", hermes_all="C_FIRE") == ["slack:C_FIRE"]


def test_firehose_keeps_the_single_channel_fallback() -> None:
    # No firehose configured → bare `slack` (the home channel), which is the
    # documented original single-channel behaviour.
    assert _firehose_targets(firehose="", hermes_all="") == ["slack"]


def _splunk_digest_targets(*, splunk: str, firehose: str, hermes_all: str) -> list[str]:
    """Resolve the splunk-domain digest deliver value the way the scheduler does."""
    suffix = _render(
        DEFAULTS["hermes_agent_hermes_all_deliver_suffix"],
        hermes_agent_slack_hermes_all_channel=hermes_all,
    )
    fire = _render(
        DEFAULTS["hermes_agent_firehose_deliver"],
        hermes_agent_slack_firehose_channel=firehose,
        hermes_agent_hermes_all_deliver_suffix=suffix,
    )
    deliver = _render(
        DEFAULTS["hermes_agent_splunk_digest_deliver"],
        hermes_agent_slack_splunk_channel=splunk,
        hermes_agent_hermes_all_deliver_suffix=suffix,
        hermes_agent_firehose_deliver=fire,
    )
    seen: list[str] = []
    for part in (p.strip() for p in deliver.split(",")):
        if part and part not in seen:
            seen.append(part)
    return seen


def test_splunk_domain_goes_to_its_own_channel_plus_hermes_all() -> None:
    assert _splunk_digest_targets(
        splunk="C_SPLUNK", firehose="C_FIRE", hermes_all="C_ALL"
    ) == ["slack:C_SPLUNK", "slack:C_ALL"]


def test_splunk_channel_unset_reproduces_todays_behaviour_exactly() -> None:
    # The empty default must be inert, not merely close: with no splunk channel
    # and hermes_all still defaulted to the firehose id, the digest resolves to
    # exactly the one target it does today.
    assert _splunk_digest_targets(
        splunk="", firehose="C_FIRE", hermes_all="C_FIRE"
    ) == ["slack:C_FIRE"]


def test_splunk_channel_unset_still_inherits_the_hermes_all_leg() -> None:
    # Falling through to the firehose target must not lose the log-of-record
    # leg once the channels diverge.
    assert _splunk_digest_targets(
        splunk="", firehose="C_FIRE", hermes_all="C_ALL"
    ) == ["slack:C_FIRE", "slack:C_ALL"]


def test_splunk_channel_is_env_sourced_never_hardcoded() -> None:
    assert "lookup('env', 'SLACK_HERMES_SPLUNK_CHANNEL')" in (
        DEFAULTS["hermes_agent_slack_splunk_channel"]
    )


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
