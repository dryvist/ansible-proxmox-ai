"""An installed direct cron job with a refused main-channel target is changed by a reversible means
where one exists, then read back from the job store; nothing is judged from an exit code or a
store that cannot be parsed.

Delivery and failure targets are edited to local by id, then each target separately, and only
then is the job withdrawn. A refused healthy footer is prompt text no cron edit reaches, so that
job is withdrawn. The job is never paused: the brain watchdog resumes paused jobs.
"""
from _cron_targets_shared import GUARD_FILE, MAIN, READBACK_FILE, ROLE, _direct, _job, _task, _tasks, _value

JOB_ID = "3f2a9c1d7e40"
ACTIVE = f"  {JOB_ID} [active]\n    Name:      pulse\n"
PAUSED = f"  {JOB_ID} [paused]\n    Name:      pulse\n"
CLEAN = [{"id": JOB_ID, "name": "pulse", "deliver": "local", "failure_deliver": "local"}]
HOME = [{"id": JOB_ID, "name": "pulse", "deliver": f"slack:{MAIN}", "failure_deliver": "local"}]
LOG = [{"id": JOB_ID, "name": "pulse", "deliver": "log", "failure_deliver": "local"}]
PARTLY = [{"id": JOB_ID, "name": "pulse", "deliver": "local", "failure_deliver": "slack:C0EXAMPLEALL"}]
MISSING_FAILURE_KEY = [{"id": JOB_ID, "name": "pulse", "deliver": "local"}]
GONE = []
REFUSED = "has a target the main-channel rule does not allow"
DELIVERY = dict(deliver=f"slack:{MAIN}", failure_deliver="local")
FOOTER = dict(deliver="local", failure_deliver="local", channel_when_healthy=f"slack:{MAIN}")
EDIT_BOTH = "Edit the installed job to local delivery and failure"
EDIT_DELIVERY = "Edit the installed job's delivery target to local"
EDIT_FAILURE = "Edit the installed job's failure target to local"


def _cron_commands(context):
    return context.get("commands", [])


def test_an_installed_job_is_edited_to_local_by_id_and_read_back_clean():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[CLEAN])
    command = _task(GUARD_FILE, EDIT_BOTH)["ansible.builtin.command"]
    rendered = " ".join(_value(command, context).split())
    assert rendered.endswith(f"cron edit {JOB_ID} --deliver local --failure-deliver local")
    assert REFUSED in message and "no longer names the main channel" in message
    assert context["marker_cleared"] is True
    assert "withdraw_called" not in context


def test_a_paused_installed_job_is_edited_too():
    message, context = _direct(_job(**DELIVERY), listing=PAUSED, reads=[CLEAN])
    assert context["hermes_agent_refuse_job_id"] == JOB_ID
    assert context["marker_cleared"] is True
    assert REFUSED in message


def test_a_job_that_is_not_installed_runs_no_cron_command():
    message, context = _direct(_job(**DELIVERY), listing="")
    assert context["hermes_agent_refuse_job_id"] == ""
    assert _cron_commands(context) == []
    assert "marker_cleared" not in context and "no longer names" not in message


def test_check_mode_runs_no_cron_command():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, check_mode=True, reads=[CLEAN])
    assert context["hermes_agent_refuse_outcome"] == "unchecked"
    assert _cron_commands(context) == []
    assert "marker_cleared" not in context


# Finding 1: a refused footer withdraws the whole job; no edit runs, and nothing is paused.
def test_a_refused_footer_withdraws_the_whole_job_and_runs_no_edit_or_pause():
    message, context = _direct(_job(**FOOTER), listing=ACTIVE, reads=[GONE])
    assert context["withdraw_called"] is True
    assert context["readback_count"] == 1
    assert not any(EDIT_BOTH in name or EDIT_DELIVERY in name for name in _cron_commands(context))
    assert not any("pause" in name.lower() for name in _cron_commands(context))
    assert "not listed in the job store" in message
    assert "STILL" not in message


def test_the_guard_task_file_never_pauses_a_job():
    text = (ROLE / "tasks" / GUARD_FILE).read_text()
    assert "cron pause" not in text
    assert "cron resume" not in text


def test_the_brain_watchdog_resumes_paused_jobs_which_is_why_the_guard_does_not_pause():
    template = (ROLE / "templates" / "hermes-brain-watchdog.sh.j2").read_text()
    assert "cron_do resume" in template
    assert "\\[paused\\]" in template


def test_a_refused_footer_job_still_listed_after_the_withdraw_fails_the_play():
    message, context = _direct(_job(**FOOTER), listing=ACTIVE, reads=[HOME])
    assert context["withdraw_called"] is True
    assert "may still post to the main channel" in message
    assert "not recorded as fixed" in message
    assert "not listed in the job store" not in message
    assert "marker_cleared" not in context


# Finding 2: a read-back that does not prove the change falls back, then withdraws, then fails closed.
def test_an_edit_that_does_not_read_back_falls_back_to_separate_edits():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[PARTLY, CLEAN])
    names = _cron_commands(context)
    assert any(EDIT_DELIVERY in name for name in names)
    assert any(EDIT_FAILURE in name for name in names)
    assert "withdraw_called" not in context
    assert "no longer names the main channel" in message
    assert context["marker_cleared"] is True


def test_when_the_separate_edits_do_not_read_back_the_job_is_withdrawn_and_read_back():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[LOG, LOG, GONE])
    assert context["withdraw_called"] is True
    assert context["readback_count"] == 3
    assert "not listed in the job store" in message
    assert "marker_cleared" not in context


def test_when_nothing_reads_back_the_play_fails_closed_naming_the_job():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[LOG, LOG, LOG])
    assert context["withdraw_called"] is True
    assert message is not None and "pulse" in message
    assert f"installed job {JOB_ID} may still post to the main channel" in message
    assert "not recorded as fixed" in message
    assert "operator" not in message.lower()
    assert "marker_cleared" not in context


def test_the_failed_path_asks_no_one_to_act_and_names_what_was_last_read_back():
    message, _ = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[LOG, LOG, LOG])
    assert "Last read back: deliver=log, failure_deliver=local" in message


# Finding 3: only exactly local on both fields is a read-back that clears the job.
def test_a_read_back_with_a_non_local_delivery_is_not_cleared():
    other = [{"id": JOB_ID, "name": "pulse", "deliver": "slack:C0EXAMPLEALL", "failure_deliver": "local"}]
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[other, other, GONE])
    assert len(_cron_commands(context)) == 3
    assert "marker_cleared" not in context


def test_a_read_back_with_a_non_local_failure_target_is_not_cleared():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[PARTLY, PARTLY, GONE])
    assert len(_cron_commands(context)) == 3
    assert "marker_cleared" not in context


def test_a_read_back_with_a_log_failure_target_is_not_cleared():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[LOG, LOG, LOG])
    assert "marker_cleared" not in context
    assert "may still post" in message


def test_a_cleared_read_back_message_says_what_was_read_back():
    message, _ = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[CLEAN])
    assert "its read-back shows deliver=local, failure_deliver=local" in message


# Finding 4: an unreadable or unparseable store fails the play; it is never read as absent.
def test_an_unparseable_store_fails_closed_and_is_not_read_as_absent():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, reads=["not json"])
    assert message is not None and "pulse" in message
    assert "cannot be read or parsed" in message and "was not judged" in message
    assert "withdraw_called" not in context
    assert "not listed in the job store" not in message
    assert "marker_cleared" not in context


def test_a_footer_job_with_an_unparseable_store_fails_the_play_without_reporting_gone():
    message, context = _direct(_job(**FOOTER), listing=ACTIVE, reads=['{"jobs": '])
    assert "cannot be read or parsed" in message
    assert "not listed in the job store" not in message


def test_a_missing_store_file_fails_closed_and_is_not_read_as_absent():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[None])
    assert "cannot be read or parsed" in message
    assert "not listed in the job store" not in message
    assert "marker_cleared" not in context


def test_a_store_entry_missing_its_failure_key_is_not_cleared():
    message, context = _direct(_job(**DELIVERY), listing=ACTIVE, reads=[MISSING_FAILURE_KEY] * 3)
    assert "marker_cleared" not in context
    assert "may still post" in message


def test_the_edits_run_before_the_withdraw_and_the_outcome_is_decided_last():
    names = [str(t.get("name", "")) for t in _tasks(GUARD_FILE)]
    order = (EDIT_BOTH, EDIT_DELIVERY, EDIT_FAILURE,
             "Withdraw the installed job when no edit reads back", "Decide the outcome for")
    positions = [next(i for i, n in enumerate(names) if n.startswith(prefix)) for prefix in order]
    assert positions == sorted(positions)


def test_the_readback_file_reads_both_fields_and_fails_closed_on_parse_errors():
    tasks = _tasks(READBACK_FILE)
    assert any("block" in task for task in tasks)
    assert any("rescue" in task for task in tasks)
    text = (ROLE / "tasks" / READBACK_FILE).read_text()
    assert "failure_deliver | default('') == 'local'" in text
