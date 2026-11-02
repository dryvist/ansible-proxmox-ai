"""Contracts for detached Laya fine-tune dispatch and collection."""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import yaml

from test_finetune_contract import (
    DEFAULTS,
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
    assert "--remain-after-exit" not in argv
    assert "--uid={{ laya_systemone_user }}" in argv
    assert "--gid={{ laya_systemone_group }}" in argv
    assert "--property=RuntimeMaxSec={{ laya_systemone_finetune_train_timeout_seconds }}" in argv
    assert "--property=ExecStopPost=+/usr/bin/systemctl start laya-systemone.service" in argv
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


def test_collect_recovers_manifest_inputs_from_the_collect_include_chain() -> None:
    references: list[Path] = []
    _load_tasks(FINETUNE_COLLECT_TASKS, references)

    recover = (ROLE / "tasks/finetune-recover.yml").resolve()
    manifest = (ROLE / "tasks/finetune-write-manifest.yml").resolve()
    promote = (ROLE / "tasks/finetune-promote.yml").resolve()
    assert recover in references
    assert references.index(recover) < references.index(manifest) < references.index(promote)

    outputs = set(DEFAULTS)
    for path in dict.fromkeys(references):
        tasks = yaml.safe_load(path.read_text())
        for task in _walk_tasks(tasks):
            if task.get("register"):
                outputs.add(task["register"])
            set_facts = task.get("ansible.builtin.set_fact", {})
            if isinstance(set_facts, dict):
                outputs.update(set_facts)

    reference_pattern = re.compile(r"\{\{.*?\}\}", re.DOTALL)
    variable_pattern = re.compile(r"\blaya_systemone_finetune_[A-Za-z0-9_]+")
    target_references = set()
    for path in (manifest, promote):
        for expression in reference_pattern.findall(path.read_text()):
            target_references.update(variable_pattern.findall(expression))

    assert target_references <= outputs
    assert "laya_systemone_finetune_laya_version" in outputs


def test_invalid_dispatch_state_probes_the_gpu_peer_and_restores_an_inactive_unit() -> None:
    train = _load_tasks()
    persisted = _find_task(train, "Persist the GPU peer state for the collect run")
    collection = _load_tasks(FINETUNE_COLLECT_TASKS)
    probe = _find_task(collection, "Read the GPU peer state when dispatch state is invalid")
    record = _find_task(collection, "Use the delegated GPU peer state for service recovery")
    start = _find_task(collection, "Start the GPU peer serving unit after fine-tuning")
    health = _find_task(collection, "Wait for GPU peer serving health after fine-tuning")

    assert "gpu_peer_was_active" in persisted["ansible.builtin.copy"]["content"]
    assert probe["ansible.builtin.command"]["argv"] == [
        "systemctl",
        "is-active",
        "--quiet",
        "{{ laya_systemone_finetune_gpu_peer_unit }}",
    ]
    assert probe["delegate_to"] == "{{ laya_systemone_finetune_gpu_peer_host }}"
    assert probe["ignore_unreachable"] is True
    assert probe["failed_when"] is False
    assert "not laya_systemone_finetune_dispatch_state_valid | bool" in probe["when"]
    assert "laya_systemone_finetune_device == 'cuda'" in probe["when"]
    assert "laya_systemone_finetune_gpu_peer_host | length > 0" in probe["when"]
    assert record["ansible.builtin.set_fact"]["laya_systemone_finetune_gpu_peer_was_active"] == (
        "{{ laya_systemone_finetune_gpu_peer_recovery_probe.rc | default(-1) == 0 }}"
    )
    assert record["ansible.builtin.set_fact"]["laya_systemone_finetune_gpu_peer_restore_required"] == (
        "{{ laya_systemone_finetune_gpu_peer_recovery_probe.rc | default(-1) == 3 }}"
    )

    environment = jinja2.Environment()
    environment.filters["bool"] = bool
    context = {
        "laya_systemone_finetune_gpu_peer_recovery_probe": {"rc": 3},
        "laya_systemone_finetune_gpu_peer_was_active": False,
    }
    restore_required = environment.from_string(
        record["ansible.builtin.set_fact"]["laya_systemone_finetune_gpu_peer_restore_required"]
    ).render(**context)
    assert restore_required == "True"
    context["laya_systemone_finetune_gpu_peer_restore_required"] = True
    assert environment.compile_expression(start["when"])(**context)
    assert environment.compile_expression(health["when"])(**context)


def test_valid_dispatch_state_with_lost_unit_validates_and_promotes_the_staged_checkpoint() -> None:
    collection = _load_tasks(FINETUNE_COLLECT_TASKS)
    classify = _find_task(collection, "Classify the detached fine-tune result without process output")
    context = {
        "laya_systemone_finetune_dispatch_state_valid": True,
        "laya_systemone_finetune_job_load_state": "not-found",
        "laya_systemone_finetune_job_sub_state": "unknown",
        "laya_systemone_finetune_job_result": "unknown",
        "laya_systemone_finetune_job_exit_status": "unknown",
    }
    environment = jinja2.Environment()
    environment.filters["bool"] = bool
    rendered = {
        key: environment.from_string(value).render(**context)
        for key, value in classify["ansible.builtin.set_fact"].items()
    }

    assert rendered["laya_systemone_finetune_job_succeeded"] == "True"
    assert rendered["laya_systemone_finetune_failed"] == "False"
    assert rendered["laya_systemone_finetune_failure_class"] == "lost-unit"

    validate = _find_task(collection, "Validate and promote a successful fine-tune checkpoint")
    assert validate["when"] == "laya_systemone_finetune_job_succeeded | bool"
    assert _find_task(validate["block"], "Validate the fine-tuned staging checkpoint")
    assert _find_task(validate["block"], "Write the fine-tune completion manifest")
    assert _find_task(validate["block"], "Promote the validated fine-tuned model")
    validation_failure = _find_task(validate["rescue"], "Classify checkpoint validation or promotion failure")
    lost_unit_failure = environment.from_string(
        validation_failure["ansible.builtin.set_fact"]["laya_systemone_finetune_failure_class"]
    ).render(laya_systemone_finetune_failure_class="lost-unit")
    assert lost_unit_failure == "lost-unit"


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


def test_collect_restarts_laya_after_promotion_and_starts_it_idempotently_afterward() -> None:
    collection = _load_tasks(FINETUNE_COLLECT_TASKS)
    restore_start = _find_task(collection, "Start Laya after GPU fine-tuning")
    promote = _load_tasks(ROLE / "tasks/finetune-promote.yml")
    move = _find_task(promote, "Move the validated staging directory into place")
    restart = _find_task(promote, "Restart Laya to load the promoted fine-tuned model")

    assert restore_start["ansible.builtin.systemd"]["state"] == "started"
    assert restart["ansible.builtin.systemd"]["state"] == "restarted"
    assert restart["when"] == "laya_systemone_finetune_promoted_unit_stat.stat.exists | default(false)"
    names = [task.get("name") for task in _walk_tasks(promote)]
    assert names.index(move["name"]) < names.index(restart["name"])


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
