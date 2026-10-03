"""Wiring checks: pr-reconcile and repo-crawl are reconciled with the right
shape, and reconcile_direct_cron.yml's new optional `script` field (monitor
mode) is actually threaded into both the create argv and the checksum.
"""
from pathlib import Path

import yaml

from _role_files import role_tasks_text

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
CRON_RECONCILE = yaml.safe_load((ROLE / "tasks" / "cron_reconcile_pr_repo_crawl.yml").read_text())


def _task(name):
    for task in CRON_RECONCILE:
        if task.get("name") == name:
            return task
    raise AssertionError(f"no task named {name!r} in cron_reconcile.yml")


def test_pr_reconcile_is_a_no_agent_script_enqueuer():
    task = _task("Reconcile the PR-reconcile cron")
    assert task["ansible.builtin.include_tasks"] == "reconcile_enqueuer_cron.yml"
    item = task["vars"]["item"]
    assert item["name"] == "{{ hermes_agent_pr_reconcile_cron_name }}"
    assert item["script"] == "{{ hermes_agent_pr_reconcile_script_name }}"
    assert item["deliver"] == "local"


def test_repo_crawl_is_a_monitor_mode_direct_cron():
    task = _task("Reconcile the repo-crawl cron")
    assert task["ansible.builtin.include_tasks"] == "reconcile_direct_cron.yml"
    item = task["vars"]["item"]
    assert item["name"] == "{{ hermes_agent_repo_crawl_cron_name }}"
    assert item["script"] == "{{ hermes_agent_repo_crawl_script_name }}"
    assert item["skill"] == "{{ hermes_agent_repo_crawl_skill }}"
    assert item["prompt_var"] == "hermes_agent_repo_crawl_cron_prompt"


def test_reconcile_direct_cron_threads_script_into_argv_and_checksum():
    text = role_tasks_text(ROLE, "reconcile_direct_cron.yml")
    assert "'--script', item.script" in text
    assert "'script': item.script | default('')" in text
