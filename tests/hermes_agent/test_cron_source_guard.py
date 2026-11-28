from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ROOT = REPO_ROOT / "roles" / "hermes_agent" / "tasks"
CHECKSUM_KEYS = (
    "schedule",
    "prompt",
    "skills",
    "script",
    "deliver",
    "failure_deliver",
    "workdir",
    "model",
    "provider",
    "base_url",
    "enabled_toolsets",
    "no_agent",
)
RUNTIME_REWRITTEN_KEYS = (
    "last_run_at",
    "last_status",
    "last_error",
    "last_delivery_error",
    "last_delivery_unverified",
    "last_failure",
    "failure_streak",
    "repeat",
    "next_run_at",
    "state",
    "enabled",
    "paused_at",
    "paused_reason",
    "latest_execution",
    "live_execution",
    "created_at",
    "schedule_display",
    "origin",
)


def _tasks(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text()) or []


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _row_checksum(row: dict) -> str:
    payload = {key: row.get(key) for key in CHECKSUM_KEYS}
    encoded = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _drift(recorded: list[dict], installed: list[dict]) -> list[dict]:
    drift = []
    for entry in recorded:
        row = next(
            (
                candidate
                for candidate in installed
                if candidate.get("store") == entry["store"]
                and candidate.get("name") == entry["name"]
            ),
            None,
        )
        identity = {"store": entry["store"], "name": entry["name"]}
        if row is None:
            drift.append(identity)
        elif row.get("enabled", True) and _row_checksum(row) != entry["sha256"]:
            drift.append(identity)
    return drift


def test_every_guard_task_uses_the_existing_switch() -> None:
    guard = _tasks(TASK_ROOT / "cron_source_guard.yml")
    assert guard
    for task in guard:
        assert "hermes_agent_source_guard_enabled | bool" in str(task.get("when", ""))


def test_checksum_contains_only_the_twelve_stable_installed_row_keys() -> None:
    rows = _tasks(TASK_ROOT / "cron_source_guard_rows.yml")
    task = _task(rows, "Direct cron | Build checksummed installed rows")
    expression = task["ansible.builtin.set_fact"]["hermes_agent_source_guard_rows"]
    match = re.search(r"'sha256':\s*\(\{(.*?)\}\s*\|\s*to_json", expression, re.DOTALL)
    assert match
    payload = match.group(1)
    pairs = re.findall(r"'([a-z_]+)': row\.get\('([a-z_]+)'\)", payload)
    assert [key for key, _ in pairs] == list(CHECKSUM_KEYS)
    assert all(key == value for key, value in pairs)
    assert all(key not in payload for key in RUNTIME_REWRITTEN_KEYS)


def test_drift_failure_precedes_the_first_reconcile_write() -> None:
    guard = _tasks(TASK_ROOT / "cron_source_guard.yml")
    fail = _task(guard, "Direct cron | Stop for scheduled jobs changed outside the declared source")
    assert "ansible.builtin.fail" in fail
    assert "_hermes_agent_direct_source_guard_drift" in str(fail.get("when", ""))
    assert "length > 0" in str(fail.get("when", ""))

    reconcile = _tasks(TASK_ROOT / "cron_reconcile.yml")
    guard_index = next(
        index
        for index, task in enumerate(reconcile)
        if task.get("ansible.builtin.include_tasks", {}).get("file") == "cron_source_guard.yml"
    )
    first_write_index = next(
        index
        for index, task in enumerate(reconcile)
        if "ansible.builtin.file" in task or "ansible.builtin.copy" in task
    )
    assert guard_index < first_write_index


def test_record_follows_direct_reconcile_and_skips_check_mode() -> None:
    reconcile = _tasks(TASK_ROOT / "cron_reconcile.yml")
    direct_files = {"reconcile_direct_cron.yml", "cron_reconcile_pr_repo_crawl.yml"}
    direct_indices = [
        index
        for index, task in enumerate(reconcile)
        if task.get("ansible.builtin.include_tasks", {}).get("file") in direct_files
    ]
    record_index = next(
        index
        for index, task in enumerate(reconcile)
        if task.get("name") == "Direct cron | Record source guard state"
    )
    record = reconcile[record_index]
    copy = record["ansible.builtin.copy"]
    assert direct_indices and record_index > max(direct_indices)
    assert "not ansible_check_mode" in str(record.get("when", ""))
    assert copy["mode"] == "0600"
    assert copy["owner"] == copy["group"] == "root"
    assert "hermes_agent_direct_cron_jobs" in copy["content"]
    assert "hermes_agent_repo_crawl_cron_name" in copy["content"]


def test_installed_row_drift_decisions() -> None:
    original = {
        "store": "store-a",
        "name": "daily-report",
        "schedule": {"kind": "cron", "expr": "12 * * * *", "display": "12 * * * *"},
        "prompt": "report prompt",
        "skills": ["report"],
        "script": None,
        "deliver": "local",
        "failure_deliver": None,
        "workdir": None,
        "model": None,
        "provider": None,
        "base_url": None,
        "enabled_toolsets": None,
        "no_agent": False,
        "enabled": True,
        "last_run_at": "old-run",
        "next_run_at": "old-next",
    }
    recorded = [{
        "store": original["store"],
        "name": original["name"],
        "sha256": _row_checksum(original),
    }]

    assert _drift([], [original]) == []

    unchanged = dict(original, last_run_at="new-run", next_run_at="new-next")
    assert _drift(recorded, [unchanged]) == []

    changed_prompt = dict(original, prompt="hand-edited prompt")
    assert _drift(recorded, [changed_prompt]) == [
        {"store": original["store"], "name": original["name"]}
    ]

    assert _drift(recorded, []) == [
        {"store": original["store"], "name": original["name"]}
    ]

    paused = dict(changed_prompt, enabled=False)
    assert _drift(recorded, [paused]) == []
