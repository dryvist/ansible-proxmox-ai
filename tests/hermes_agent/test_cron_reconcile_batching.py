"""A converge lists installed jobs and reads markers once; an unchanged job makes no remote call."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml
from jinja2 import Environment

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TASKS = ROLE / "tasks"
REMOTE_MODULES = ("ansible.builtin.command", "ansible.builtin.copy", "ansible.builtin.file")
HOME = "/home/hermes/.hermes"
GITHUB_MAINT = f"{HOME}/profiles/github-maint"
SPLUNK_ADMIN = f"{HOME}/profiles/splunk-admin"
ENV = Environment()
ENV.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "yes", "1")
ENV.filters["regex_escape"] = re.escape
ENV.filters["regex_search"] = lambda value, pattern: re.search(pattern, value)
ENV.filters["basename"] = lambda path: path.rsplit("/", 1)[-1]
# Ansible's map(attribute=..., default=...) over the dict items these tasks use.
ENV.filters["map"] = lambda items, attribute, default=None: [i.get(attribute, default) for i in items]
WANTED = yaml.safe_load(
    (ROLE / "defaults" / "main" / "30-cron-identities.yml").read_text()
)["hermes_agent_direct_job_wanted"]


def _load(name: str) -> list[dict]:
    return yaml.safe_load((TASKS / name).read_text())


def _batch() -> list[dict]:
    return _load("cron_reconcile_direct_batch.yml")[0]["block"]


def _named(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _value(expression: str, **context):
    """Render one role expression the way Ansible does, as a Python value."""
    return ast.literal_eval(ENV.from_string(expression).render(**context))


def _wanted(job: dict, *, host: str) -> bool:
    """The role's single enablement definition. Jobs without `identities` take the default [hermes]."""
    return _value(WANTED, item=job, hermes_agent_id=host,
                  hermes_agent_job_identities=job.get("identities", ["hermes"]))


def _batch_facts(jobs: list[dict], *, host: str, existing: set[str]):
    """Run the batch's home tasks over one job table. `existing` lists the homes that exist.

    Returns (homes listed, names of jobs the fail task rejects, fail message per job).
    """
    batch = _batch()
    context = {
        "hermes_agent_hermes_home": HOME,
        "hermes_agent_direct_cron_jobs": jobs,
        "ansible_check_mode": False,
        "hermes_agent_install": {"changed": False},
    }
    declared = _value(
        _named(batch, "Direct cron | Collect every declared job home")
        ["ansible.builtin.set_fact"]["hermes_agent_direct_declared_homes"],
        **context,
    )
    # Ansible's stat omits `isdir` for a missing path, so the shape matches that.
    context["hermes_agent_direct_home_stats"] = {"results": [
        {"item": home, "stat": {"exists": True, "isdir": True} if home in existing
         else {"exists": False}}
        for home in declared
    ]}
    listed = _value(
        _named(batch, "Direct cron | Index the declared job homes that exist")
        ["ansible.builtin.set_fact"]["hermes_agent_direct_existing_homes"],
        **context,
    )
    context["hermes_agent_direct_existing_homes"] = listed
    fail = _named(batch, "Direct cron | Fail when a wanted job's profile home does not exist")
    failing, messages = [], {}
    for job in jobs:
        job_context = {**context, "item": job, "hermes_agent_direct_job_wanted": _wanted(job, host=host)}
        if all(ENV.compile_expression(cond)(**job_context) for cond in fail["when"]):
            failing.append(job["name"])
            messages[job["name"]] = ENV.from_string(fail["ansible.builtin.fail"]["msg"]).render(**job_context)
    return listed, failing, messages


def _take_list_fails(job: dict, *, host: str, lists: dict[str, str]) -> bool:
    """Whether the per-job 'Take the installed job list' task fails for one job."""
    task = next(
        t for t in _load("reconcile_direct_cron.yml")
        if t.get("name", "").startswith("Direct cron | Take the installed job list")
    )
    return bool(ENV.compile_expression(task["failed_when"])(
        item=job,
        ansible_check_mode=False,
        hermes_agent_install={"changed": False},
        hermes_agent_hermes_home=HOME,
        hermes_agent_direct_cron_lists=lists,
        hermes_agent_direct_wanted=_wanted(job, host=host),
    ))


def _ran(*, wanted: bool, listed: bool | str, marker: str | None, changed: bool) -> set[str]:
    """Names of the remote/CLI tasks whose `when` passes for one job."""
    ctx = {
        "item": {"name": "job-a"},
        "hermes_agent_direct_wanted": wanted,
        "hermes_agent_direct_cron_list": {
            "stdout": f"Name: {listed}" if isinstance(listed, str)
            else "Name: job-a" if listed else ""
        },
        "hermes_agent_direct_markers": {"job-a": marker} if marker else {},
        "hermes_agent_direct_marker_probe": {"changed": changed},
        "ansible_check_mode": False,
    }
    ran = set()
    for task in _load("reconcile_direct_cron.yml"):
        if not any(m in task for m in REMOTE_MODULES):
            continue
        conds = task.get("when", [])
        conds = [conds] if isinstance(conds, str) else conds
        if all(ENV.compile_expression(str(c))(**ctx) for c in conds):
            ran.add(task["name"].split(" {{")[0])
    return ran


def test_matching_marker_makes_no_cli_or_remote_call():
    assert _ran(wanted=True, listed=True, marker="sum", changed=False) == set()


def test_drifted_job_is_removed_recreated_and_recorded():
    assert _ran(wanted=True, listed=True, marker="old", changed=True) == {
        "Direct cron | Remove drifted",
        "Direct cron | Create",
        "Direct cron | Record desired state for",
    }


def test_absent_job_is_created_and_recorded_without_a_remove():
    assert _ran(wanted=True, listed=False, marker=None, changed=True) == {
        "Direct cron | Create",
        "Direct cron | Record desired state for",
    }


def test_job_name_substring_does_not_count_as_an_exact_match():
    assert _ran(wanted=True, listed="job-ab", marker=None, changed=True) == {
        "Direct cron | Create",
        "Direct cron | Record desired state for",
    }


def test_withdrawn_job_is_removed_and_its_marker_cleared_even_after_a_stale_probe():
    assert _ran(wanted=False, listed=True, marker="sum", changed=True) == {
        "Direct cron | Remove job not owned by this agent:",
        "Direct cron | Clear desired-state marker for withdrawn",
    }


def test_unowned_job_with_nothing_installed_makes_no_remote_call():
    assert _ran(wanted=False, listed=False, marker=None, changed=False) == set()


def test_jobs_never_run_cron_list_and_the_batch_runs_it_per_home():
    per_job = [t for t in _load("reconcile_direct_cron.yml") if "cron list" in str(t)]
    assert per_job == []
    reconcile = _load("cron_reconcile.yml")
    batch_include = next(
        i for i, task in enumerate(reconcile)
        if task.get("ansible.builtin.include_tasks") == "cron_reconcile_direct_batch.yml"
    )
    direct_loop = next(
        i for i, task in enumerate(reconcile)
        if task.get("name") == "Reconcile the agentic direct-deliver digest crons"
    )
    assert batch_include < direct_loop
    batch = _batch()
    listing = [t for t in batch if "cron list --all" in str(t)]
    assert len(listing) == 1 and "hermes_agent_direct_existing_homes" in listing[0]["loop"]
    assert sum("grep" in str(t) for t in batch) == 1


def test_gated_off_job_on_a_missing_profile_home_is_neither_listed_nor_failed():
    jobs = [
        {"name": "disabled-here", "enabled": False, "hermes_home": GITHUB_MAINT},
        {"name": "owned-elsewhere", "enabled": True, "hermes_home": GITHUB_MAINT,
         "identities": ["other-agent"]},
    ]
    listed, failing, _ = _batch_facts(jobs, host="hermes-private", existing={HOME})
    assert GITHUB_MAINT not in listed
    assert failing == []
    assert not any(_take_list_fails(job, host="hermes-private", lists={HOME: ""}) for job in jobs)


def test_gated_off_job_on_an_existing_profile_home_stays_listed_so_it_can_be_withdrawn():
    job = {"name": "moved-away", "enabled": True, "hermes_home": SPLUNK_ADMIN,
           "identities": ["other-agent"]}
    listed, failing, _ = _batch_facts([job], host="hermes", existing={HOME, SPLUNK_ADMIN})
    assert SPLUNK_ADMIN in listed
    assert failing == []


def test_enabled_job_on_an_existing_profile_home_is_listed_and_reconciled():
    job = {"name": "splunk-health", "enabled": True, "hermes_home": SPLUNK_ADMIN}
    assert _wanted(job, host="hermes") is True
    listed, failing, _ = _batch_facts([job], host="hermes", existing={HOME, SPLUNK_ADMIN})
    assert SPLUNK_ADMIN in listed
    assert failing == []
    assert _take_list_fails(job, host="hermes", lists={HOME: "", SPLUNK_ADMIN: ""}) is False
    assert _ran(wanted=_wanted(job, host="hermes"), listed=False, marker=None, changed=True) == {
        "Direct cron | Create",
        "Direct cron | Record desired state for",
    }


def test_enabled_job_on_a_missing_profile_home_fails_naming_the_profile_and_path():
    job = {"name": "github-review", "enabled": True, "hermes_home": GITHUB_MAINT}
    listed, failing, messages = _batch_facts([job], host="hermes", existing={HOME})
    assert GITHUB_MAINT not in listed
    assert failing == ["github-review"]
    assert "'github-maint'" in messages["github-review"]
    assert f"'{GITHUB_MAINT}'" in messages["github-review"]
