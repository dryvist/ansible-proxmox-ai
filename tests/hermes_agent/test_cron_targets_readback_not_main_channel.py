"""A refused main-channel target on an installed direct cron job is edited or withdrawn, then read back.

Delivery and failure targets are edited to local by id. A healthy footer, which is prompt text,
is withdrawn with the direct-cron withdraw tasks. Either way the job store decides the outcome:
the marker is cleared only when the read-back proves the job no longer names the main channel,
and the failure message says "STILL posting" whenever it cannot prove that.
"""
from _cron_targets_shared import GUARD_FILE, MAIN, _direct, _job, _task, _tasks, _value

JOB_ID = "3f2a9c1d7e40"
ACTIVE = f"  {JOB_ID} [active]\n    Name:      pulse\n"
PAUSED = f"  {JOB_ID} [paused]\n    Name:      pulse\n"
STORE_CLEAN = [{"id": JOB_ID, "name": "pulse", "deliver": "local", "failure_deliver": "local"}]
STORE_HOME = [{"id": JOB_ID, "name": "pulse", "deliver": f"slack:{MAIN}", "failure_deliver": "local"}]
STORE_OTHER = [{"id": "f00ba4f00ba4", "name": "pulse", "deliver": "local", "failure_deliver": "local"}]
REFUSED = "has a target the main-channel rule does not allow"
DELIVERY = dict(deliver=f"slack:{MAIN}", failure_deliver="local")
FOOTER = dict(deliver="local", failure_deliver="local", channel_when_healthy=f"slack:{MAIN}")


def test_an_installed_job_is_edited_to_local_by_id():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, store=STORE_CLEAN)
    command = _task(GUARD_FILE, "Edit the installed job")["ansible.builtin.command"]
    rendered = " ".join(_value(command, context).split())
    assert rendered.endswith(f"cron edit {JOB_ID} --deliver local --failure-deliver local")
    assert REFUSED in message
    assert "withdraw_called" not in context


def test_a_paused_installed_job_is_edited_too():
    message, context = _direct(_job(**DELIVERY), listing=PAUSED, store=STORE_CLEAN)
    assert context["hermes_agent_refuse_job_id"] == JOB_ID
    assert context["marker_cleared"] is True
    assert REFUSED in message


def test_a_job_that_is_not_installed_is_not_edited():
    message, context = _direct(_job(**DELIVERY), listing="")
    assert context["hermes_agent_refuse_job_id"] == ""
    assert "STILL" not in message and "no longer names" not in message
    assert "marker_cleared" not in context


def test_check_mode_does_not_edit_the_installed_job():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, check_mode=True, store=STORE_CLEAN)
    assert context["hermes_agent_refuse_outcome"] == "unchecked"
    assert "STILL" not in message and "no longer names" not in message
    assert "marker_cleared" not in context


def test_a_clean_read_back_clears_the_marker_and_fails_with_the_refusal():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, store=STORE_CLEAN)
    assert context["marker_cleared"] is True
    assert "no longer names the main channel" in message
    assert "re-run the reconcile" in message


def test_a_read_back_that_still_names_the_home_channel_fails_as_still_posting():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, store=STORE_HOME)
    assert f"{JOB_ID} for pulse is STILL" in message
    assert "marker_cleared" not in context


def test_a_missing_job_id_after_the_edit_fails_as_still_posting():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, store=STORE_OTHER)
    assert f"{JOB_ID} for pulse is STILL" in message
    assert "marker_cleared" not in context


def test_a_failed_edit_still_ends_in_the_refusal():
    message, _ = _direct(_job(**DELIVERY), listing=ACTIVE, store=STORE_CLEAN, edit_rc=1)
    assert REFUSED in message
    assert "STILL" not in message


def test_a_failed_edit_with_the_job_still_naming_home_fails_as_still_posting():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, store=STORE_HOME, edit_rc=1)
    assert "STILL" in message
    assert "marker_cleared" not in context


def test_a_store_entry_missing_its_failure_key_is_still_posting():
    store = [{"id": JOB_ID, "name": "pulse", "deliver": "local"}]
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, store=store)
    assert f"{JOB_ID} for pulse is STILL" in message
    assert "marker_cleared" not in context


def test_a_store_entry_missing_both_target_keys_is_still_posting():
    store = [{"id": JOB_ID, "name": "pulse"}]
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, store=store)
    assert f"{JOB_ID} for pulse is STILL" in message
    assert "marker_cleared" not in context


def test_a_missing_store_file_is_still_posting():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, store=None)
    assert f"{JOB_ID} for pulse is STILL" in message
    assert "marker_cleared" not in context


def test_a_refused_footer_withdraws_the_installed_job_and_the_removal_is_read_back():
    message, context = _direct(_job(**FOOTER), listing=ACTIVE, store=[])
    assert context["withdraw_called"] is True
    assert context["hermes_agent_refuse_edit"] == {}
    assert "was removed" in message
    assert "returns when the declared target is corrected and the reconcile is re-run" in message
    assert "STILL" not in message


def test_a_refused_footer_still_present_after_the_withdraw_fails_as_still_posting():
    message, context = _direct(_job(**FOOTER), listing=ACTIVE, store=STORE_CLEAN)
    assert context["withdraw_called"] is True
    assert f"{JOB_ID} for pulse is STILL" in message
    assert "removed" not in message
    assert "marker_cleared" not in context


def test_a_refused_footer_on_an_uninstalled_job_does_not_withdraw():
    message, context = _direct(_job(**FOOTER), listing="", store=[])
    assert "withdraw_called" not in context
    assert "STILL" not in message and "was removed" not in message


def test_check_mode_does_not_withdraw_for_a_refused_footer():
    message, context = _direct(_job(**FOOTER), listing=ACTIVE, check_mode=True, store=[])
    assert "withdraw_called" not in context
    assert "was removed" not in message


def test_the_installed_job_is_edited_or_withdrawn_before_the_check_fails():
    names = [str(t.get("name", "")) for t in _tasks(GUARD_FILE)]
    order = ("Edit the installed job", "Withdraw the installed job for a refused healthy footer",
             "Read the installed job back", "Select the installed job", "Compare the read-back",
             "Decide the outcome", "Clear the desired-state marker", "Refuse the main channel as a target")
    positions = [next(i for i, n in enumerate(names) if n.startswith(prefix)) for prefix in order]
    assert positions == sorted(positions)
