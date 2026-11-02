"""Contracts for detached Laya fine-tune service recovery."""

from __future__ import annotations

import jinja2

from test_finetune_contract import (
    DEFAULTS,
    FINETUNE_COLLECT_TASKS,
    FINETUNE_TASKS,
    ROLE,
    _find_task,
    _load_tasks,
    _walk_tasks,
)


def test_dispatch_state_is_read_tolerantly_and_peer_restores_before_non_tolerant_laya_tasks() -> None:
    collection = _load_tasks(FINETUNE_COLLECT_TASKS)
    tasks = list(_walk_tasks(collection))
    state_stat = _find_task(collection, "Read persisted dispatch state before querying the transient unit")
    state_file = _find_task(collection, "Read the persisted dispatch state when it is present")
    job_state = _find_task(collection, "Read the Laya fine-tune systemd unit state")
    peer_start = _find_task(collection, "Start the GPU peer serving unit after fine-tuning")

    assert state_stat["ignore_unreachable"] is True
    assert state_file["ignore_unreachable"] is True
    assert tasks.index(state_stat) < tasks.index(state_file) < tasks.index(job_state)
    remote_modules = {
        "ansible.builtin.command",
        "ansible.builtin.file",
        "ansible.builtin.stat",
        "ansible.builtin.slurp",
        "ansible.builtin.systemd",
        "ansible.builtin.uri",
    }
    for task in tasks[: tasks.index(peer_start)]:
        if not task.get("delegate_to") and remote_modules.intersection(task):
            assert task.get("ignore_unreachable") is True, task.get("name")
    first_non_tolerant_laya = next(
        index
        for index, task in enumerate(tasks)
        if not task.get("delegate_to")
        and remote_modules.intersection(task)
        and not task.get("ignore_unreachable")
    )
    assert tasks.index(peer_start) < first_non_tolerant_laya


def test_unreachable_laya_results_are_recovery_failures() -> None:
    report = (ROLE / "tasks/finetune-report-failure.yml").read_text()
    results = (
        "laya_systemone_finetune_dispatch_state_stat",
        "laya_systemone_finetune_dispatch_state_file",
        "laya_systemone_finetune_job_state_result",
        "laya_systemone_finetune_unit_stat",
        "laya_systemone_finetune_service_start",
        "laya_systemone_finetune_health",
        "laya_systemone_finetune_job_stop",
        "laya_systemone_finetune_job_reset",
    )
    for result in results:
        assert f"{result}.unreachable" in report


def test_dispatch_state_is_retained_when_recovery_does_not_finish() -> None:
    collection = _load_tasks(FINETUNE_COLLECT_TASKS)
    readiness = _find_task(collection, "Evaluate service readiness before clearing fine-tune state")
    cleanup_ready = _find_task(collection, "Confirm recovery and transient-unit cleanup completed")
    clear_state = _find_task(collection, "Clear completed fine-tune dispatch state after releasing the lock")
    peer_start = _find_task(collection, "Start the GPU peer serving unit after fine-tuning")
    peer_health = _find_task(collection, "Wait for GPU peer serving health after fine-tuning")

    readiness_text = readiness["ansible.builtin.set_fact"]["laya_systemone_finetune_services_ready"]
    assert "laya_systemone_finetune_gpu_peer_start.failed" in readiness_text
    assert "laya_systemone_finetune_gpu_peer_health.status" in readiness_text
    assert "laya_systemone_finetune_services_ready | bool" in cleanup_ready[
        "ansible.builtin.set_fact"
    ]["laya_systemone_finetune_recovery_cleanup_ready"]
    assert "laya_systemone_finetune_cleanup_allowed | bool" in clear_state["when"]
    assert peer_start["ignore_errors"] is True
    assert peer_health["ignore_errors"] is True

    environment = jinja2.Environment()
    environment.filters["bool"] = bool
    expression = cleanup_ready["ansible.builtin.set_fact"]["laya_systemone_finetune_recovery_cleanup_ready"]
    rendered = environment.from_string(expression).render(
        laya_systemone_finetune_services_ready=False,
        laya_systemone_finetune_job_load_state="loaded",
        laya_systemone_finetune_job_stop={},
        laya_systemone_finetune_job_reset={},
    )
    assert rendered == "False"


def test_health_timeout_and_both_check_budgets_fit_collect_limit() -> None:
    restore = _load_tasks(ROLE / "tasks/finetune-restore-services.yml")
    health_tasks = (
        _find_task(restore, "Wait for Laya health after GPU fine-tuning"),
        _find_task(restore, "Wait for GPU peer serving health after fine-tuning"),
    )
    for task in health_tasks:
        assert task["ansible.builtin.uri"]["timeout"] == (
            "{{ laya_systemone_finetune_health_timeout_seconds }}"
        )

    worst_case = 2 * (DEFAULTS["laya_systemone_finetune_health_retries"] + 1) * (
        DEFAULTS["laya_systemone_finetune_health_delay_seconds"]
        + DEFAULTS["laya_systemone_finetune_health_timeout_seconds"]
    )
    assert worst_case == 294
    assert worst_case < 600
    defaults = (ROLE / "defaults/main/20-finetune.yml").read_text()
    assert "294 s" in defaults and "600 s" in defaults


def test_atomic_dispatch_lock_is_taken_before_dataset_or_model_preparation() -> None:
    dispatch = _load_tasks(FINETUNE_TASKS)
    tasks = list(_walk_tasks(dispatch))
    lock = _find_task(dispatch, "Atomically acquire the per-host fine-tune dispatch lock")
    datasets = _find_task(dispatch, "Download and verify the fine-tuning datasets")
    base = _find_task(dispatch, "Fetch and verify the pinned Laya base checkpoint")
    argv = lock["ansible.builtin.command"]["argv"]

    assert argv[:4] == ["mkdir", "-m", "0700", "--"]
    assert argv[4] == "{{ laya_systemone_finetune_dispatch_lock_path }}"
    assert lock["register"] == "laya_systemone_finetune_dispatch_lock_create"
    assert tasks.index(lock) < tasks.index(datasets) < tasks.index(base)


def test_unknown_unit_state_refuses_dispatch_and_collects_without_service_changes() -> None:
    dispatch = _load_tasks(FINETUNE_TASKS)
    guard = _find_task(dispatch, "Refuse overlapping or uncollected fine-tune jobs")
    condition = " ".join(guard["ansible.builtin.assert"]["that"])
    assert "laya_systemone_finetune_job_state_valid | bool" in condition

    reader = _load_tasks(ROLE / "tasks/finetune-read-job-state.yml")
    validate = _find_task(reader, "Validate the Laya fine-tune systemd unit state")
    validity = validate["ansible.builtin.set_fact"]["laya_systemone_finetune_job_state_valid"]
    assert "job_state_result.rc | default(1) == 0" in validity
    assert "job_load_state != 'unknown'" in validity
    assert "job_active_state != 'unknown'" in validity

    collection = _load_tasks(FINETUNE_COLLECT_TASKS)
    unknown = _find_task(collection, "Refuse collection when the transient unit state is unknown")
    peer_start = _find_task(collection, "Start the GPU peer serving unit after fine-tuning")
    tasks = list(_walk_tasks(collection))
    assert "not laya_systemone_finetune_job_state_valid | bool" in " ".join(unknown["when"])
    assert tasks.index(unknown) < tasks.index(peer_start)
