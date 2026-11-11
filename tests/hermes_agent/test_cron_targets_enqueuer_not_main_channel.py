"""Script-fed enqueuer crons of a guarded identity never deliver, or fail, to its main channel.

reconcile_enqueuer_cron.yml includes the same guard as the direct path, with the
enqueuer's resolved deliver and the shared failure channel. Ownership comes from the
roster's system mapping, so these fixtures map the splunk system to the fixture identity.
"""
import pytest

from _cron_targets_shared import DEFAULTS, FIXTURE, ISSUES, MAIN, _base, _enqueuer, _enqueuer_passes, _value

OWNED = {"splunk": FIXTURE}


def _enqueuer_job(**fields):
    return {"name": "splunk-status-digest", "system": "splunk", "enabled": True, **fields}


def test_an_enqueuer_delivering_to_the_main_channel_fails():
    assert not _enqueuer_passes(_enqueuer_job(deliver=f"slack:{MAIN}", script="status.py"), owners=OWNED)


def test_an_enqueuer_failing_into_the_main_channel_fails():
    assert not _enqueuer_passes(_enqueuer_job(deliver="local"), owners=OWNED, failure=f"slack:{MAIN}")


def test_an_enqueuer_without_deliver_stays_local():
    assert _enqueuer_passes(_enqueuer_job(), owners=OWNED)


def test_a_disabled_enqueuer_is_not_checked():
    assert _enqueuer_passes(_enqueuer_job(enabled=False, deliver=f"slack:{MAIN}"), owners=OWNED)


def test_an_enqueuer_owned_by_another_identity_is_not_checked():
    assert _enqueuer_passes(_enqueuer_job(deliver=f"slack:{MAIN}"), owners={})


def test_an_unset_home_channel_fails_for_an_owned_enqueuer():
    assert not _enqueuer_passes(_enqueuer_job(deliver="local"), owners=OWNED, home="")


def test_a_bare_channel_id_in_an_enqueuer_target_fails():
    assert not _enqueuer_passes(_enqueuer_job(deliver="C0EXAMPLEALL"), owners=OWNED)


def test_an_enqueuer_failure_names_the_job_and_the_rejected_entry():
    message, _ = _enqueuer(_enqueuer_job(deliver="slack:C0EXAMPLEALL:1700.1"), owners=OWNED)
    assert message is not None
    assert "splunk-status-digest" in message and "slack:C0EXAMPLEALL:1700.1" in message


# The hermes-private Splunk channel no longer defaults to its home channel. A digest that still
# lands on home through a fallback is refused by the same guard.
def test_the_splunk_channel_default_does_not_fall_back_to_the_home_channel():
    assert "hermes_agent_slack_home_channel" not in DEFAULTS["hermes_agent_slack_splunk_channel"]


def test_a_hermes_private_splunk_digest_that_lands_on_home_is_refused():
    assert not _enqueuer_passes(_enqueuer_job(deliver=f"slack:{MAIN}"),
                                identity="hermes-private", owners={"splunk": "hermes-private"})


HERMES_PRIVATE = {"splunk": "hermes-private"}
# The three Splunk jobs of an identity: the status digest, the triage digests and the trajectory watch.
SPLUNK_CHANNELS = [
    "hermes_agent_splunk_status_digest_channel",
    "hermes_agent_triage_channel",
    "hermes_agent_wired_trajectory_channel",
]


def _splunk_channel(name, splunk_channel=""):
    """The channel a Splunk job of hermes-private resolves to; an empty splunk_channel is an unset variable."""
    context = _base({}, "hermes-private", MAIN, f"slack:{ISSUES}", {}, {})
    context["hermes_agent_slack_splunk_channel"] = splunk_channel
    return _value(DEFAULTS[name], context)


@pytest.mark.parametrize("name", SPLUNK_CHANNELS)
def test_a_hermes_private_splunk_job_that_falls_back_to_home_is_refused(name):
    assert _splunk_channel(name) == MAIN
    assert not _enqueuer_passes(_enqueuer_job(deliver=f"slack:{MAIN}"), identity="hermes-private",
                                owners=HERMES_PRIVATE)


@pytest.mark.parametrize("name", SPLUNK_CHANNELS)
def test_a_hermes_private_splunk_job_on_its_own_channel_passes(name):
    channel = _splunk_channel(name, splunk_channel="C0EXAMPLESPLUNK")
    assert channel == "C0EXAMPLESPLUNK"
    assert _enqueuer_passes(_enqueuer_job(deliver=f"slack:{channel}"), identity="hermes-private",
                            owners=HERMES_PRIVATE)


def test_the_refusal_for_a_hermes_private_splunk_job_names_the_splunk_channel_setting():
    message, _ = _enqueuer(_enqueuer_job(deliver=f"slack:{MAIN}"), identity="hermes-private",
                           owners=HERMES_PRIVATE)
    assert "SLACK_HERMES_PRIVATE_SPLUNK_CHANNEL" in message
