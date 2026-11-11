"""Shared helpers for the main-channel guard tests.

The guard is refuse_main_channel_target.yml. These helpers resolve each job's
targets with the real set_fact tasks of the two reconcile files, then run the
guard's own tasks in order with Jinja, so the tests exercise the written
expressions rather than a copy of them.
"""
import re
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment

from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
GUARD_FILE = "refuse_main_channel_target.yml"
MAIN = "C0EXAMPLEMAIN"
ISSUES = "C0EXAMPLEISS"
HOME_PATH = "/var/lib/hermes"
# A fixture identity that is neither a shipped identity nor the opt-out.
FIXTURE = "alpha"


def _search(value, pattern, group):
    found = re.search(pattern, value)
    if not found:
        return None
    return found.group(int(group[1:])) if group else found.group(0)


ENV = NativeEnvironment()
ENV.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
ENV.filters["bool"] = lambda value: value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "1")
ENV.filters["regex_search"] = _search
ENV.filters["regex_escape"] = lambda value: re.escape(str(value))


def _tasks(file):
    return yaml.safe_load((ROLE / "tasks" / file).read_text())


def _task(file, prefix):
    return next(t for t in _tasks(file) if str(t.get("name", "")).startswith(prefix))


def _value(value, context):
    """Evaluate one task value: a `{{ }}` template, or a bare `when:`/`that:` expression."""
    if not isinstance(value, str) or not value.strip():
        return value
    if "{{" in value:
        return ENV.from_string(value).render(**context)
    return ENV.compile_expression(value)(**context)


def _conditions(task):
    when = task.get("when", [])
    return [when] if isinstance(when, str) else when


def _base(job, identity, home, failure, owners, profiles, listing="", check_mode=False, refuse=None):
    context = {
        "item": job,
        "hermes_agent_id": identity,
        "hermes_agent_identity": identity,
        "hermes_agent_slack_home_channel": home,
        "hermes_agent_cron_failure_deliver": failure,
        "hermes_agent_cron_model": "",
        "hermes_agent_cron_model_provider": "custom",
        "hermes_agent_hermes_home": HOME_PATH,
        "hermes_agent_direct_no_agent": False,
        "hermes_agent_slack_profile_channels": profiles,
        "hermes_agent_job_identities_default": DEFAULTS["hermes_agent_job_identities_default"],
        "hermes_agent_direct_cron_lists": {HOME_PATH: listing},
        "hermes_agent_managed_cron_state_dir": "/var/lib/hermes/state",
        "hermes_agent_bin": "hermes",
        "hermes_agent_cli_profile_args": [],
        "hermes_agent_user": "hermes",
        "ansible_check_mode": check_mode,
        "ai_agent_owner": owners,
    }
    for name in ("hermes_agent_digest_slack_channel", "hermes_agent_job_identities",
                 "hermes_agent_direct_job_wanted", "hermes_agent_slack_hermes_all_channel",
                 "hermes_agent_refuse_main_channel_targets"):
        context[name] = _value(DEFAULTS[name], context)
    if refuse is not None:
        context["hermes_agent_refuse_main_channel_targets"] = refuse
    return context


def _resolve(task, context):
    for name, value in task.get("vars", {}).items():
        context[name] = _value(value, context)
    for name, value in task.get("ansible.builtin.set_fact", {}).items():
        context[name] = _value(value, context)
    return context


def _guard_run(caller_file, include_prefix, context):
    """Run the caller's include gate, then the guard's tasks in order.

    Returns (failure message or None, final context). A skipped check returns None.
    """
    include = _task(caller_file, include_prefix)
    if not all(_value(c, context) for c in _conditions(include)):
        return None, context
    context = {**context, **{k: _value(v, context) for k, v in include["vars"].items()}}
    for task in _tasks(GUARD_FILE):
        if task.get("register"):
            context[task["register"]] = {}
        scope = {**context, **{k: _value(v, context) for k, v in task.get("vars", {}).items()}}
        if not all(_value(c, scope) for c in _conditions(task)):
            continue
        if "ansible.builtin.set_fact" in task:
            for k, v in task["ansible.builtin.set_fact"].items():
                context[k] = _value(v, scope)
        elif "ansible.builtin.command" in task:
            context[task["register"]] = {"changed": True}
        elif "ansible.builtin.assert" in task:
            if not all(_value(c, scope) for c in task["ansible.builtin.assert"]["that"]):
                return _value(task["ansible.builtin.assert"]["fail_msg"], scope), context
    return None, context


def _job(**fields):
    return {"name": "pulse", "schedule": "39 * * * *", "enabled": True, "identities": [FIXTURE], **fields}


def _shipped_job(name, identity=FIXTURE, home=MAIN):
    """The shipped core direct-cron job called `name`, enabled, with its templates rendered."""
    scope = {**DEFAULTS, "hermes_agent_id": identity, "hermes_agent_slack_home_channel": home}
    scope["hermes_agent_slack_hermes_all_channel"] = _value(
        DEFAULTS["hermes_agent_slack_hermes_all_channel"], scope)
    job = next(j for j in DEFAULTS["hermes_agent_direct_cron_jobs_core"]
               if _value(j["name"], scope) == name)
    return {**job, "name": name, "enabled": True, "deliver": _value(job["deliver"], scope)}


def _direct(job, identity=FIXTURE, home=MAIN, failure=f"slack:{ISSUES}", profiles=None, listing="",
            check_mode=False, refuse=None):
    context = _base(job, identity, home, failure, {}, profiles or {}, listing, check_mode, refuse)
    context = _resolve(_task("reconcile_direct_cron.yml", "Direct cron | Decide agent ownership"), context)
    context = _resolve(_task("reconcile_direct_cron.yml", "Direct cron | Resolve deliver target"), context)
    return _guard_run("reconcile_direct_cron.yml", "Direct cron | Refuse the main channel", context)


def _enqueuer(job, identity=FIXTURE, home=MAIN, failure=f"slack:{ISSUES}", owners=None, refuse=None):
    context = _base(job, identity, home, failure, owners or {}, {}, refuse=refuse)
    context = _resolve(_task("reconcile_enqueuer_cron.yml", "Enqueuer cron | Effective-active"), context)
    context = _resolve(_task("reconcile_enqueuer_cron.yml", "Enqueuer cron | Resolve fields"), context)
    return _guard_run("reconcile_enqueuer_cron.yml", "Enqueuer cron | Refuse the main channel", context)


def _direct_passes(job, **kwargs):
    return _direct(job, **kwargs)[0] is None


def _enqueuer_passes(job, **kwargs):
    return _enqueuer(job, **kwargs)[0] is None
