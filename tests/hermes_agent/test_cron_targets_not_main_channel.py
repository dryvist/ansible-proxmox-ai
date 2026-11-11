"""A Donna cron job may never deliver, or fail, to her main channel.

The guards sit in the two reconcile files that compute each job's final targets.
These tests render the real set_fact and assert expressions from those files, so
defaults, profile remaps and channel_when_healthy are checked as they resolve.
"""
import re
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment

from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
MAIN = "C0EXAMPLEMAIN"
ISSUES = "C0EXAMPLEISS"
ENV = NativeEnvironment()
ENV.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
ENV.filters["bool"] = lambda value: value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "1")


def _task(file, prefix):
    tasks = yaml.safe_load((ROLE / "tasks" / file).read_text())
    return next(t for t in tasks if str(t.get("name", "")).startswith(prefix))


def _value(value, context):
    """Evaluate one task value: a `{{ }}` template, or a bare `when:`/`that:` expression."""
    if not isinstance(value, str):
        return value
    if "{{" in value:
        return ENV.from_string(value).render(**context)
    return ENV.compile_expression(value)(**context)


def _base(job, identity, home, failure, owners, profiles):
    context = {
        "item": job,
        "hermes_agent_id": identity,
        "hermes_agent_identity": identity,
        "hermes_agent_slack_home_channel": home,
        "hermes_agent_cron_failure_deliver": failure,
        "hermes_agent_cron_model": "",
        "hermes_agent_cron_model_provider": "custom",
        "hermes_agent_hermes_home": "/var/lib/hermes",
        "hermes_agent_direct_no_agent": False,
        "hermes_agent_slack_profile_channels": profiles,
        "hermes_agent_job_identities_default": DEFAULTS["hermes_agent_job_identities_default"],
        "ai_agent_owner": owners,
    }
    for name in ("hermes_agent_digest_slack_channel", "hermes_agent_job_identities",
                 "hermes_agent_direct_job_wanted"):
        context[name] = _value(DEFAULTS[name], context)
    return context


def _resolve(task, context):
    for name, value in task.get("vars", {}).items():
        context[name] = _value(value, context)
    for name, value in task.get("ansible.builtin.set_fact", {}).items():
        context[name] = _value(value, context)
    return context


def _guard_passes(file, prefix, context):
    task = _task(file, prefix)
    if not all(_value(cond, context) for cond in task["when"]):
        return True
    context = {**context, **{k: _value(v, context) for k, v in task.get("vars", {}).items()}}
    return all(_value(cond, context) for cond in task["ansible.builtin.assert"]["that"])


def _direct_passes(job, identity="donna", home=MAIN, failure=f"slack:{ISSUES}", profiles=None):
    context = _base(job, identity, home, failure, {}, profiles or {})
    context = _resolve(_task("reconcile_direct_cron.yml", "Direct cron | Decide agent ownership"), context)
    context = _resolve(_task("reconcile_direct_cron.yml", "Direct cron | Resolve deliver target"), context)
    return _guard_passes("reconcile_direct_cron.yml", "Direct cron | Refuse Donna", context)


def _enqueuer_passes(job, identity="donna", home=MAIN, failure=f"slack:{ISSUES}", owners=None):
    context = _base(job, identity, home, failure, owners or {}, {})
    context = _resolve(_task("reconcile_enqueuer_cron.yml", "Enqueuer cron | Effective-active"), context)
    context = _resolve(_task("reconcile_enqueuer_cron.yml", "Enqueuer cron | Resolve fields"), context)
    return _guard_passes("reconcile_enqueuer_cron.yml", "Enqueuer cron | Refuse Donna", context)


def _donna_job(**fields):
    return {"name": "pulse", "schedule": "39 * * * *", "enabled": True, "identities": ["donna"], **fields}


def test_donna_jobs_aimed_at_their_own_channels_pass():
    assert _direct_passes(_donna_job(deliver="slack:C0EXAMPLEALL", failure_deliver=f"slack:{ISSUES}"))
    assert _direct_passes(_donna_job(deliver="local", failure_deliver="local"))


def test_delivery_to_the_main_channel_fails():
    assert not _direct_passes(_donna_job(deliver=f"slack:{MAIN}", failure_deliver="local"))


def test_a_target_list_naming_the_main_channel_fails():
    assert not _direct_passes(_donna_job(deliver=f"slack:C0EXAMPLEALL,slack:{MAIN}", failure_deliver="local"))


def test_failure_to_the_main_channel_fails():
    assert not _direct_passes(_donna_job(deliver="slack:C0EXAMPLEALL", failure_deliver=f"slack:{MAIN}"))


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


def test_an_unset_home_channel_is_not_checked():
    assert _direct_passes(_donna_job(deliver="slack:", failure_deliver="local"), home="")


def test_a_donna_enqueuer_delivering_to_the_main_channel_fails():
    job = {"name": "splunk-status-digest", "system": "splunk", "enabled": True,
           "deliver": f"slack:{MAIN}", "script": "status.py"}
    assert not _enqueuer_passes(job, owners={"splunk": "donna"})


def test_a_donna_enqueuer_failing_into_the_main_channel_fails():
    job = {"name": "splunk-status-digest", "system": "splunk", "enabled": True, "deliver": "local"}
    assert not _enqueuer_passes(job, owners={"splunk": "donna"}, failure=f"slack:{MAIN}")


def test_a_donna_enqueuer_without_deliver_stays_local():
    job = {"name": "splunk-status-digest", "system": "splunk", "enabled": True}
    assert _enqueuer_passes(job, owners={"splunk": "donna"})


def test_a_disabled_enqueuer_is_not_checked():
    job = {"name": "splunk-status-digest", "system": "splunk", "enabled": False, "deliver": f"slack:{MAIN}"}
    assert _enqueuer_passes(job, owners={"splunk": "donna"})


def test_an_enqueuer_owned_by_another_identity_is_not_checked():
    job = {"name": "splunk-status-digest", "system": "splunk", "enabled": True, "deliver": f"slack:{MAIN}"}
    assert _enqueuer_passes(job, owners={})


def test_each_guard_runs_before_any_job_is_created_or_removed():
    for file, guard, mutations in (
        ("reconcile_direct_cron.yml", "Direct cron | Refuse Donna",
         ("Direct cron | Remove", "Direct cron | Create")),
        ("reconcile_enqueuer_cron.yml", "Enqueuer cron | Refuse Donna",
         ("Enqueuer cron | Remove", "Enqueuer cron | Create")),
    ):
        names = [str(t.get("name", "")) for t in yaml.safe_load((ROLE / "tasks" / file).read_text())]
        first_mutation = min(i for i, n in enumerate(names) if n.startswith(mutations))
        assert names.index(next(n for n in names if n.startswith(guard))) < first_mutation


def test_the_old_top_level_guard_is_gone():
    names = [str(t.get("name", "")) for t in yaml.safe_load((ROLE / "tasks" / "cron_reconcile.yml").read_text())]
    assert not any(n.startswith("Refuse a Donna cron job") for n in names)
