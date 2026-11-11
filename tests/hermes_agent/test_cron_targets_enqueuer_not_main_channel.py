"""Script-fed enqueuer crons of a guarded identity never deliver, or fail, to its main channel.

reconcile_enqueuer_cron.yml includes the same guard as the direct path, with the
enqueuer's resolved deliver and the shared failure channel. Ownership comes from the
roster's system mapping, so these fixtures map the splunk system to the fixture identity.
"""
from _cron_targets_shared import FIXTURE, MAIN, _enqueuer, _enqueuer_passes

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
