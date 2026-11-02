"""Contracts for detached Laya fine-tune dispatch and collection."""

from __future__ import annotations

from pathlib import Path

from test_finetune_contract import (
    FINETUNE_COLLECT_TASKS,
    FINETUNE_TASKS,
    ROLE,
    _find_task,
    _load_tasks,
    _walk_tasks,
)


def test_dispatch_starts_a_detached_journal_job_without_waiting() -> None:
    dispatch = _load_tasks()
    launch = _find_task(dispatch, "Launch the Laya training command as a detached systemd job")
    argv = launch["ansible.builtin.command"]["argv"]

    assert argv[0] == "systemd-run"
    assert "--no-block" in argv
    assert "--remain-after-exit" in argv
    assert "--uid={{ laya_systemone_user }}" in argv
    assert "--gid={{ laya_systemone_group }}" in argv
    assert "--property=RuntimeMaxSec={{ laya_systemone_finetune_train_timeout_seconds }}" in argv
    assert "--property=StandardOutput=journal" in argv
    assert "--property=StandardError=journal" in argv
    assert "--setenv=HF_HUB_OFFLINE=1" in argv
    assert "--setenv=TRANSFORMERS_OFFLINE=1" in argv
    assert launch["no_log"] is True
    assert "async" not in launch and "poll" not in launch
    for task_file in ROLE.joinpath("tasks").glob("finetune*.yml"):
        assert "async:" not in task_file.read_text()


def test_dispatch_refuses_an_active_job_and_a_pending_collect_state() -> None:
    dispatch = _load_tasks()
    refuse = next(task for task in dispatch if task.get("name") == "Refuse overlapping or uncollected fine-tune jobs")
    conditions = " ".join(refuse["ansible.builtin.assert"]["that"])

    assert "laya_systemone_finetune_job_active_state not in ['active', 'activating', 'deactivating', 'reloading']" in conditions
    assert "not laya_systemone_finetune_dispatch_state_stat.stat.exists" in conditions


def test_collect_returns_while_running_before_it_can_touch_services() -> None:
    collection = _load_tasks(FINETUNE_COLLECT_TASKS)
    running = _find_task(collection, "Report that the detached Laya fine-tune job is still running")
    stat = _find_task(collection, "Check for persisted dispatch state after the job finishes")
    finish = _find_task(collection, "Collect every finished Laya fine-tune job")

    assert running["ansible.builtin.debug"]["msg"] == "running"
    assert running["when"] == "laya_systemone_finetune_job_running | bool"
    assert stat["when"] == "not laya_systemone_finetune_job_running | bool"
    assert "not laya_systemone_finetune_job_running | bool" in finish["when"]
    assert _find_task(finish["block"], "Start Laya after GPU fine-tuning")


def test_collect_restarts_the_gpu_peer_only_when_dispatch_state_says_it_was_active() -> None:
    train = _load_tasks()
    persisted = _find_task(train, "Persist the GPU peer state for the collect run")
    collection = _load_tasks(FINETUNE_COLLECT_TASKS)
    start = _find_task(collection, "Start the GPU peer serving unit after fine-tuning")
    health = _find_task(collection, "Wait for GPU peer serving health after fine-tuning")

    assert "gpu_peer_was_active" in persisted["ansible.builtin.copy"]["content"]
    assert start["when"] == "laya_systemone_finetune_gpu_peer_was_active | default(false) | bool"
    assert health["when"] == "laya_systemone_finetune_gpu_peer_was_active | default(false) | bool"


def test_restarts_and_health_waits_ignore_errors_before_the_final_failure() -> None:
    collection = _load_tasks(FINETUNE_COLLECT_TASKS)
    names = [task.get("name") for task in _walk_tasks(collection)]
    recovery_names = (
        "Start Laya after GPU fine-tuning",
        "Start the GPU peer serving unit after fine-tuning",
        "Wait for Laya health after GPU fine-tuning",
        "Wait for GPU peer serving health after fine-tuning",
    )
    for name in recovery_names:
        assert _find_task(collection, name)["ignore_errors"] is True
    final_fail = _find_task(collection, "Report the fine-tuning failure without exposing dataset output")
    assert final_fail["ansible.builtin.fail"]
    assert all(names.index(name) < names.index(final_fail["name"]) for name in recovery_names)


def test_dispatch_and_collect_share_recovery_and_failure_reporting_tasks() -> None:
    dispatch_refs: list[Path] = []
    collect_refs: list[Path] = []
    _load_tasks(FINETUNE_TASKS, dispatch_refs)
    _load_tasks(FINETUNE_COLLECT_TASKS, collect_refs)
    restore = (ROLE / "tasks/finetune-restore-services.yml").resolve()
    report = (ROLE / "tasks/finetune-report-failure.yml").resolve()

    assert dispatch_refs.count(restore) == 1
    assert collect_refs.count(restore) == 1
    assert dispatch_refs.count(report) == 1
    assert collect_refs.count(report) == 1
