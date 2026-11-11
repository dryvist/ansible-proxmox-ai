"""A Donna cron job may never deliver, or fail, to her main channel."""
from pathlib import Path

import yaml
from jinja2 import Environment

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TASK = "Refuse a Donna cron job whose delivery or failure target is her main channel"
MAIN = "C0EXAMPLEMAIN"


def _task():
    tasks = yaml.safe_load((ROLE / "tasks" / "cron_reconcile.yml").read_text())
    return next(t for t in tasks if t["name"] == TASK)


def _passes(job, identity="donna", home=MAIN):
    task = _task()
    env = Environment()
    context = {"item": job, "hermes_agent_identity": identity, "hermes_agent_slack_home_channel": home}
    if not all(env.compile_expression(cond)(**context) for cond in task["when"]):
        return True
    context["_main_channel_target"] = env.from_string(task["vars"]["_main_channel_target"]).render(**context)
    return all(env.compile_expression(cond)(**context) for cond in task["ansible.builtin.assert"]["that"])


def test_jobs_aimed_at_the_all_and_issues_channels_pass():
    assert _passes({"name": "pulse", "deliver": "slack:C0EXAMPLEALL", "failure_deliver": "slack:C0EXAMPLEISS"})
    assert _passes({"name": "pulse", "deliver": "local", "failure_deliver": "local"})


def test_delivery_to_the_main_channel_fails():
    assert not _passes({"name": "pulse", "deliver": f"slack:{MAIN}", "failure_deliver": "local"})


def test_failure_to_the_main_channel_fails():
    assert not _passes({"name": "pulse", "deliver": "slack:C0EXAMPLEALL", "failure_deliver": f"slack:{MAIN}"})


def test_other_identities_and_an_unset_home_channel_are_not_checked():
    job = {"name": "digest", "deliver": f"slack:{MAIN}", "failure_deliver": f"slack:{MAIN}"}
    assert _passes(job, identity="hermes")
    assert _passes(job, home="")


def test_the_guard_runs_before_any_cron_job_is_created():
    names = [t["name"] for t in yaml.safe_load((ROLE / "tasks" / "cron_reconcile.yml").read_text())]
    assert names.index(TASK) == 0
