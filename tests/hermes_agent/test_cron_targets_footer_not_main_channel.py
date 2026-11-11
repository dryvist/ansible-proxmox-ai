"""A healthy footer decides whether an installed direct cron job has its whole job withdrawn.

A footer that names the main channel in any form, or names a channel this rule cannot show to be
another one, withdraws the whole job and reads it back. A read-back that shows only the delivery
and failure targets as local never clears such a job. A footer naming another slack:<ID> is accepted.
"""
import pytest

from _cron_targets_shared import MAIN, _direct, _job

JOB_ID = "3f2a9c1d7e40"
ACTIVE = f"  {JOB_ID} [active]\n    Name:      pulse\n"
LOCAL_BOTH = [{"id": JOB_ID, "name": "pulse", "deliver": "local", "failure_deliver": "local"}]
GONE = []
EDIT_BOTH = "Edit the installed job to local delivery and failure"

# Each footer names the main channel in a form the rule must recognise, or names a channel that
# cannot be shown to be another one (a #name has no ID to compare with the home channel's ID).
WITHDRAWN_FOOTERS = [
    f"slack:{MAIN}",
    f"slack:{MAIN.lower()}",
    MAIN,
    f"slack:{MAIN}:1700.123456",
    f"{MAIN}:1700.123456",
    f"slack:C0EXAMPLEALL,slack:{MAIN}",
    "#general",
    "slack:#general",
]


def _footer_job(footer):
    return _job(deliver="local", failure_deliver="local", channel_when_healthy=footer)


@pytest.mark.parametrize("footer", WITHDRAWN_FOOTERS)
def test_a_footer_that_may_name_the_main_channel_withdraws_the_whole_job_and_reads_it_back_gone(footer):
    message, context = _direct(_footer_job(footer), listing=ACTIVE, reads=[GONE])
    assert context["withdraw_called"] is True
    assert context["readback_count"] == 1
    assert not any(EDIT_BOTH in name for name in context.get("commands", []))
    assert "marker_cleared" not in context
    assert "not listed in the job store" in message


@pytest.mark.parametrize("footer", WITHDRAWN_FOOTERS)
def test_a_footer_that_may_name_the_main_channel_is_never_cleared_by_a_clean_read_back(footer):
    message, context = _direct(_footer_job(footer), listing=ACTIVE, reads=[LOCAL_BOTH])
    assert "marker_cleared" not in context
    assert "may still post to the main channel" in message
    assert "no longer names the main channel" not in message


def test_a_bare_non_home_footer_is_refused_as_a_form_and_not_withdrawn():
    message, context = _direct(_footer_job("C0EXAMPLEALL"), listing=ACTIVE, reads=[LOCAL_BOTH])
    assert "withdraw_called" not in context
    assert any(EDIT_BOTH in name for name in context.get("commands", []))
    assert "has a target the main-channel rule does not allow" in message


@pytest.mark.parametrize("footer", ["slack:C0EXAMPLEALL", "local", ""])
def test_a_footer_naming_another_slack_channel_or_no_channel_is_accepted(footer):
    message, context = _direct(_footer_job(footer), listing=ACTIVE)
    assert message is None
    assert "withdraw_called" not in context
