"""A direct cron job never delivers, or fails, to its identity's main channel while the switch is on.

The rule is refuse_main_channel_target.yml, included per job by reconcile_direct_cron.yml
after it resolves the job's targets. These tests resolve the targets with the real set_fact
tasks, then run the guard's own tasks. The enqueuer path is in its own file. The switch
hermes_agent_refuse_main_channel_targets defaults on for every identity but hermes.
"""
import yaml

from _cron_targets_shared import (
    FIXTURE, GUARD_FILE, ISSUES, MAIN, ROLE, _base, _direct, _direct_passes, _job, _shipped_job, _task, _tasks,
    _value,
)

JOB_ID = "3f2a9c1d7e40"
ACTIVE = f"  {JOB_ID} [active]\n    Name:      pulse\n"
PAUSED = f"  {JOB_ID} [paused]\n    Name:      pulse\n"
STORE_CLEAN = [{"id": JOB_ID, "name": "pulse", "deliver": "local", "failure_deliver": "local"}]
STORE_HOME = [{"id": JOB_ID, "name": "pulse", "deliver": f"slack:{MAIN}", "failure_deliver": "local"}]
STORE_OTHER = [{"id": "f00ba4f00ba4", "name": "pulse", "deliver": "local"}]
REFUSED = "has a target the main-channel rule does not allow"


def test_jobs_aimed_at_their_own_channels_pass():
    assert _direct_passes(_job(deliver="slack:C0EXAMPLEALL", failure_deliver=f"slack:{ISSUES}"))
    assert _direct_passes(_job(deliver="local", failure_deliver="local"))


def test_delivery_to_the_main_channel_fails():
    assert not _direct_passes(_job(deliver=f"slack:{MAIN}", failure_deliver="local"))


def test_a_target_list_naming_the_main_channel_fails():
    assert not _direct_passes(_job(deliver=f"slack:C0EXAMPLEALL,slack:{MAIN}", failure_deliver="local"))


def test_failure_to_the_main_channel_fails():
    assert not _direct_passes(_job(deliver="slack:C0EXAMPLEALL", failure_deliver=f"slack:{MAIN}"))


def test_a_failure_list_naming_the_main_channel_fails():
    assert not _direct_passes(_job(deliver="slack:C0EXAMPLEALL",
                                   failure_deliver=f"slack:{ISSUES},slack:{MAIN}"))


def test_a_job_with_no_deliver_falls_back_to_the_home_channel_and_fails():
    assert not _direct_passes(_job(failure_deliver="local"))


def test_a_failure_with_no_override_falls_back_to_the_shared_channel_and_fails():
    assert not _direct_passes(_job(deliver="local"), failure=f"slack:{MAIN}")


def test_a_profile_route_into_the_main_channel_fails():
    job = _job(deliver="slack:C0EXAMPLEALL", failure_deliver="local",
               hermes_home="/var/lib/hermes/profiles/splunk-admin")
    assert not _direct_passes(job, profiles={"splunk-admin": MAIN})
    assert _direct_passes(job, profiles={"splunk-admin": "C0EXAMPLEALL"})


def test_a_healthy_footer_into_the_main_channel_fails():
    assert not _direct_passes(_job(deliver="local", failure_deliver="local",
                                   channel_when_healthy=f"slack:{MAIN}"))


def test_a_disabled_job_is_not_checked():
    assert _direct_passes(_job(enabled=False, deliver=f"slack:{MAIN}", failure_deliver=f"slack:{MAIN}"))


def test_a_job_owned_by_another_identity_is_not_checked():
    assert _direct_passes(_job(identities=["hermes"], deliver=f"slack:{MAIN}",
                               failure_deliver=f"slack:{MAIN}"))


def test_hermes_is_not_checked_while_its_switch_is_off():
    job = _job(identities=["hermes"], deliver=f"slack:{MAIN}", failure_deliver=f"slack:{MAIN}")
    assert _direct_passes(job, identity="hermes")
    _, context = _direct(job, identity="hermes")
    assert context["hermes_agent_refuse_main_channel_targets"] is False


def test_the_switch_defaults_on_for_every_identity_but_hermes():
    for identity, expected in (("hermes", False), (FIXTURE, True), ("beta", True)):
        assert _base(_job(), identity, MAIN, "local", {}, {})["hermes_agent_refuse_main_channel_targets"] is expected


def test_an_unset_home_channel_fails_when_the_switch_is_on_and_is_not_checked_when_off():
    assert not _direct_passes(_job(deliver="slack:", failure_deliver="local"), home="")
    job = _job(identities=["hermes"], deliver="slack:C0EXAMPLEALL", failure_deliver="local")
    assert _direct_passes(job, identity="hermes", home="")


def test_a_bare_channel_id_in_a_list_fails():
    assert not _direct_passes(_job(deliver=f"local,{MAIN}", failure_deliver="local"))
    assert not _direct_passes(_job(deliver="local", failure_deliver="C0EXAMPLEALL"))


def test_a_thread_suffix_fails():
    assert not _direct_passes(_job(deliver="slack:C0EXAMPLEALL:1700.1", failure_deliver="local"))


def test_a_lowercase_channel_id_fails():
    assert not _direct_passes(_job(deliver="slack:c0exampleall", failure_deliver="local"))


def test_a_hash_channel_name_fails():
    assert not _direct_passes(_job(deliver="slack:#general", failure_deliver="local"))


def test_a_lowercase_home_channel_still_blocks_the_upper_case_form():
    assert not _direct_passes(_job(deliver=f"slack:{MAIN}", failure_deliver="local"), home=MAIN.lower())


def test_a_padded_entry_naming_the_main_channel_fails():
    assert not _direct_passes(_job(deliver=f" slack:{MAIN} ", failure_deliver="local"))


def test_the_allowed_forms_pass():
    assert _direct_passes(_job(deliver="slack:C0EXAMPLEALL", failure_deliver="log"))
    assert _direct_passes(_job(deliver="local,", failure_deliver=" local "))
    assert _direct_passes(_job(deliver="slack:C0EXAMPLEALL,local", failure_deliver=f"slack:{ISSUES}"))


def test_the_failure_names_the_job_and_the_rejected_entry():
    message, _ = _direct(_job(deliver="slack:C0EXAMPLEALL:1700.1", failure_deliver="local"))
    assert message is not None
    assert "pulse" in message and "slack:C0EXAMPLEALL:1700.1" in message


def test_an_installed_job_is_edited_to_local_by_id():
    message, context = _direct(_job(deliver=f"slack:{MAIN}", failure_deliver="local"),
                               listing=ACTIVE, store=STORE_CLEAN)
    command = _task(GUARD_FILE, "Edit the installed job")["ansible.builtin.command"]
    rendered = " ".join(_value(command, context).split())
    assert rendered.endswith(f"cron edit {JOB_ID} --deliver local --failure-deliver local")
    assert REFUSED in message


def test_a_paused_installed_job_is_edited_too():
    message, context = _direct(_job(deliver=f"slack:{MAIN}", failure_deliver="local"),
                               listing=PAUSED, store=STORE_CLEAN)
    assert context["hermes_agent_refuse_job_id"] == JOB_ID
    assert context["marker_cleared"] is True
    assert REFUSED in message


def test_a_job_that_is_not_installed_is_not_edited():
    message, context = _direct(_job(deliver=f"slack:{MAIN}", failure_deliver="local"), listing="")
    assert context["hermes_agent_refuse_job_id"] == ""
    assert "STILL" not in message and "no longer names" not in message
    assert "marker_cleared" not in context


def test_check_mode_does_not_edit_the_installed_job():
    message, context = _direct(_job(deliver=f"slack:{MAIN}", failure_deliver="local"), listing=ACTIVE,
                               check_mode=True, store=STORE_CLEAN)
    assert context["hermes_agent_refuse_outcome"] == "unchecked"
    assert "STILL" not in message and "no longer names" not in message
    assert "marker_cleared" not in context


def test_a_clean_read_back_clears_the_marker_and_fails_with_the_refusal():
    message, context = _direct(_job(deliver=f"slack:{MAIN}", failure_deliver="local"),
                               listing=ACTIVE, store=STORE_CLEAN)
    assert context["marker_cleared"] is True
    assert "no longer names the main channel" in message
    assert "re-run the reconcile" in message


def test_a_read_back_that_still_names_the_home_channel_fails_as_still_posting():
    message, context = _direct(_job(deliver=f"slack:{MAIN}", failure_deliver="local"),
                               listing=ACTIVE, store=STORE_HOME)
    assert f"{JOB_ID} for pulse is STILL" in message
    assert "marker_cleared" not in context


def test_a_missing_job_id_after_the_edit_fails_as_still_posting():
    message, context = _direct(_job(deliver=f"slack:{MAIN}", failure_deliver="local"),
                               listing=ACTIVE, store=STORE_OTHER)
    assert f"{JOB_ID} for pulse is STILL" in message
    assert "marker_cleared" not in context


def test_a_failed_edit_still_ends_in_the_refusal():
    message, _ = _direct(_job(deliver=f"slack:{MAIN}", failure_deliver="local"),
                         listing=ACTIVE, store=STORE_CLEAN, edit_rc=1)
    assert REFUSED in message
    assert "STILL" not in message


def test_a_failed_edit_with_the_job_still_naming_home_fails_as_still_posting():
    message, context = _direct(_job(deliver=f"slack:{MAIN}", failure_deliver="local"),
                               listing=ACTIVE, store=STORE_HOME, edit_rc=1)
    assert "STILL" in message
    assert "marker_cleared" not in context


def test_a_healthy_footer_naming_home_is_never_reported_as_fixed():
    message, context = _direct(_job(deliver="local", failure_deliver="local",
                                    channel_when_healthy=f"slack:{MAIN}"),
                               listing=ACTIVE, store=STORE_CLEAN)
    assert "STILL" in message
    assert "marker_cleared" not in context


def test_the_installed_job_is_edited_before_the_check_fails():
    names = [str(t.get("name", "")) for t in _tasks(GUARD_FILE)]
    order = ("Edit the installed job", "Read the installed job back", "Select the installed job",
             "Compare the read-back", "Decide the outcome", "Clear the desired-state marker",
             "Refuse the main channel as a target")
    positions = [next(i for i, n in enumerate(names) if n.startswith(prefix)) for prefix in order]
    assert positions == sorted(positions)


def test_the_switch_turned_on_for_hermes_refuses_daily_summary():
    job = _shipped_job("daily-summary", identity="hermes")
    assert job["deliver"] == f"slack:{MAIN}"
    assert _direct_passes(job, identity="hermes")
    message, _ = _direct(job, identity="hermes", refuse=True)
    assert message is not None
    assert "daily-summary" in message and f"slack:{MAIN}" in message


def test_a_run_logs_one_line_naming_the_identity_when_the_switch_is_off():
    task = _task("cron_reconcile.yml", "Note that main-channel")
    assert "{{ hermes_agent_id }}" in task["ansible.builtin.debug"]["msg"]
    for identity, shown in (("hermes", True), (FIXTURE, False)):
        context = _base(_job(), identity, MAIN, "local", {}, {})
        assert _value(task["when"], context) is shown


def test_each_guard_runs_before_any_job_is_created_or_removed():
    for file, guard, mutations in (
        ("reconcile_direct_cron.yml", "Direct cron | Refuse the main channel",
         ("Direct cron | Remove", "Direct cron | Create")),
        ("reconcile_enqueuer_cron.yml", "Enqueuer cron | Refuse the main channel",
         ("Enqueuer cron | Remove", "Enqueuer cron | Create")),
    ):
        tasks = yaml.safe_load((ROLE / "tasks" / file).read_text())
        names = [str(t.get("name", "")) for t in tasks]
        first_mutation = min(i for i, n in enumerate(names) if n.startswith(mutations))
        include = next(t for t in tasks if str(t.get("name", "")).startswith(guard))
        target = include["ansible.builtin.include_tasks"]["file"]
        assert target == GUARD_FILE
        assert names.index(include["name"]) < first_mutation


def test_the_old_top_level_guard_is_gone():
    names = [str(t.get("name", "")) for t in yaml.safe_load((ROLE / "tasks" / "cron_reconcile.yml").read_text())]
    assert not any(n.startswith("Refuse a ") for n in names)
