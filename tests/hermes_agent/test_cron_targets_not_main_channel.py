"""Donna's direct cron jobs never deliver, or fail, to her main channel.

The rule is refuse_main_channel_target.yml, included per job by reconcile_direct_cron.yml
after it resolves the job's targets. These tests resolve the targets with the real set_fact
tasks, then run the guard's own tasks. The enqueuer path is in its own file.
"""
import yaml

from _cron_targets_shared import GUARD_FILE, ISSUES, MAIN, ROLE, _direct, _direct_passes, _donna_job, _tasks

ACTIVE = "  3f2a9c1d7e40 [active]\n    Name:      pulse\n"
PAUSED = "  3f2a9c1d7e40 [paused]\n    Name:      pulse\n"


def test_donna_jobs_aimed_at_their_own_channels_pass():
    assert _direct_passes(_donna_job(deliver="slack:C0EXAMPLEALL", failure_deliver=f"slack:{ISSUES}"))
    assert _direct_passes(_donna_job(deliver="local", failure_deliver="local"))


def test_delivery_to_the_main_channel_fails():
    assert not _direct_passes(_donna_job(deliver=f"slack:{MAIN}", failure_deliver="local"))


def test_a_target_list_naming_the_main_channel_fails():
    assert not _direct_passes(_donna_job(deliver=f"slack:C0EXAMPLEALL,slack:{MAIN}", failure_deliver="local"))


def test_failure_to_the_main_channel_fails():
    assert not _direct_passes(_donna_job(deliver="slack:C0EXAMPLEALL", failure_deliver=f"slack:{MAIN}"))


def test_a_failure_list_naming_the_main_channel_fails():
    assert not _direct_passes(_donna_job(deliver="slack:C0EXAMPLEALL",
                                         failure_deliver=f"slack:{ISSUES},slack:{MAIN}"))


def test_a_job_with_no_deliver_falls_back_to_the_home_channel_and_fails():
    assert not _direct_passes(_donna_job(failure_deliver="local"))


def test_a_failure_with_no_override_falls_back_to_the_shared_channel_and_fails():
    assert not _direct_passes(_donna_job(deliver="local"), failure=f"slack:{MAIN}")


def test_a_profile_route_into_the_main_channel_fails():
    job = _donna_job(deliver="slack:C0EXAMPLEALL", failure_deliver="local",
                     hermes_home="/var/lib/hermes/profiles/splunk-admin")
    assert not _direct_passes(job, profiles={"splunk-admin": MAIN})
    assert _direct_passes(job, profiles={"splunk-admin": "C0EXAMPLEALL"})


def test_a_healthy_footer_into_the_main_channel_fails():
    assert not _direct_passes(_donna_job(deliver="local", failure_deliver="local",
                                         channel_when_healthy=f"slack:{MAIN}"))


def test_a_disabled_job_is_not_checked():
    assert _direct_passes(_donna_job(enabled=False, deliver=f"slack:{MAIN}", failure_deliver=f"slack:{MAIN}"))


def test_a_job_owned_by_another_identity_is_not_checked():
    assert _direct_passes(_donna_job(identities=["hermes"], deliver=f"slack:{MAIN}",
                                     failure_deliver=f"slack:{MAIN}"))


def test_other_identities_are_not_checked():
    job = _donna_job(identities=["hermes"], deliver=f"slack:{MAIN}", failure_deliver=f"slack:{MAIN}")
    assert _direct_passes(job, identity="hermes")


def test_an_unset_home_channel_fails_for_donna_and_is_not_checked_for_others():
    assert not _direct_passes(_donna_job(deliver="slack:", failure_deliver="local"), home="")
    job = _donna_job(identities=["hermes"], deliver="local", failure_deliver="local")
    assert _direct_passes(job, identity="hermes", home="")


def test_a_bare_channel_id_in_a_list_fails():
    assert not _direct_passes(_donna_job(deliver=f"local,{MAIN}", failure_deliver="local"))
    assert not _direct_passes(_donna_job(deliver="local", failure_deliver="C0EXAMPLEALL"))


def test_a_thread_suffix_fails():
    assert not _direct_passes(_donna_job(deliver="slack:C0EXAMPLEALL:1700.1", failure_deliver="local"))


def test_a_lowercase_channel_id_fails():
    assert not _direct_passes(_donna_job(deliver="slack:c0exampleall", failure_deliver="local"))


def test_a_hash_channel_name_fails():
    assert not _direct_passes(_donna_job(deliver="slack:#general", failure_deliver="local"))


def test_a_lowercase_home_channel_still_blocks_the_upper_case_form():
    assert not _direct_passes(_donna_job(deliver=f"slack:{MAIN}", failure_deliver="local"), home=MAIN.lower())


def test_a_padded_entry_naming_the_main_channel_fails():
    assert not _direct_passes(_donna_job(deliver=f" slack:{MAIN} ", failure_deliver="local"))


def test_the_allowed_forms_pass():
    assert _direct_passes(_donna_job(deliver="slack:C0EXAMPLEALL", failure_deliver="log"))
    assert _direct_passes(_donna_job(deliver="local,", failure_deliver=" local "))
    assert _direct_passes(_donna_job(deliver="slack:C0EXAMPLEALL,local", failure_deliver=f"slack:{ISSUES}"))


def test_the_failure_names_the_job_and_the_rejected_entry():
    message, _ = _direct(_donna_job(deliver="slack:C0EXAMPLEALL:1700.1", failure_deliver="local"))
    assert message is not None
    assert "pulse" in message and "slack:C0EXAMPLEALL:1700.1" in message


def test_an_installed_active_job_is_paused_when_its_target_is_refused():
    message, context = _direct(_donna_job(deliver=f"slack:{MAIN}", failure_deliver="local"), listing=ACTIVE)
    assert context["hermes_agent_refuse_job_id"] == "3f2a9c1d7e40"
    assert context["hermes_agent_refuse_job_state"] == "active"
    assert "was paused" in message
    assert "re-run the reconcile" in message


def test_a_job_that_is_not_installed_is_not_paused():
    message, context = _direct(_donna_job(deliver=f"slack:{MAIN}", failure_deliver="local"), listing="")
    assert context["hermes_agent_refuse_job_id"] == ""
    assert "was paused" not in message


def test_an_already_paused_job_is_not_paused_again():
    message, context = _direct(_donna_job(deliver=f"slack:{MAIN}", failure_deliver="local"), listing=PAUSED)
    assert context["hermes_agent_refuse_job_state"] == "paused"
    assert "was paused" not in message


def test_check_mode_does_not_pause_the_installed_job():
    message, _ = _direct(_donna_job(deliver=f"slack:{MAIN}", failure_deliver="local"), listing=ACTIVE,
                         check_mode=True)
    assert "was paused" not in message


def test_the_installed_job_is_paused_before_the_check_fails():
    names = [str(t.get("name", "")) for t in _tasks(GUARD_FILE)]
    pause = next(i for i, n in enumerate(names) if n.startswith("Pause "))
    marker = next(i for i, n in enumerate(names) if n.startswith("Clear the desired-state marker"))
    check = next(i for i, n in enumerate(names) if n.startswith("Refuse Donna's main channel"))
    assert pause < marker < check


def test_each_guard_runs_before_any_job_is_created_or_removed():
    for file, guard, mutations in (
        ("reconcile_direct_cron.yml", "Direct cron | Refuse Donna",
         ("Direct cron | Remove", "Direct cron | Create")),
        ("reconcile_enqueuer_cron.yml", "Enqueuer cron | Refuse Donna",
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
    assert not any(n.startswith("Refuse a Donna cron job") for n in names)
