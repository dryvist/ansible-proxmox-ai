from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_TASKS = REPO_ROOT / "roles" / "hermes_agent" / "tasks"

SCHEDULER_TASKS = {
    "Route a self-declared cron failure through _cron_route",
    "Deliver a self-declared cron failure through the failure lane",
    "Route unresolved_origin through the failure lane for a declared failure",
}
LEDGER_TASK = "Route the run-ledger classification through the failure lane for a declared failure"


def _load_tasks() -> list[dict]:
    return yaml.safe_load((ROLE_TASKS / "patches_cron_failure_routing.yml").read_text())


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def test_cron_failure_routing_preflights_all_anchors_before_replacing() -> None:
    patch_tasks = _load_tasks()
    names = [task.get("name") for task in patch_tasks]
    guard = names.index(
        "Validate every cron failure-routing patch anchor before changing source"
    )
    first_replace = names.index(
        "Route a self-declared cron failure through _cron_route"
    )
    assert guard < first_replace

    conditions = patch_tasks[guard]["ansible.builtin.assert"]["that"]
    assert len(conditions) == 2
    assert all("hermes_agent_cron_failure_routing_source_text" in c for c in conditions)
    assert conditions[0].count(" is not none") == 4
    assert " or (" in conditions[0]
    assert "d.declared_failure = _cron_declared_failure" in conditions[0]
    assert "def _cron_route(" in conditions[1]
    assert "hermes_agent_cron_failure_bookkeeping_source_text" in conditions[0]

    first_regexp = patch_tasks[first_replace]["ansible.builtin.replace"]["regexp"]
    assert (
        "output_file=output_file, agent_declared=d\\.agent_declared\\)$"
        in first_regexp
    )
    assert (
        "(?!\\n[ \\t]*job, deliver_content, _cron_declared)"
        in first_regexp
    )
    replacement = patch_tasks[first_replace]["ansible.builtin.replace"]["replace"]
    source = (
        "    (\n"
        "        deliver_content, d.blocked_config, _silent_alert, d.incident_acked, "
        "d.failure_incident_id,\n"
        "    ) = _compose_run_delivery(\n"
        "        job, success=d.success, error=d.error, final_response=final_response,\n"
        "        output_file=output_file, agent_declared=d.agent_declared)\n"
    )
    patched, count = re.subn(
        first_regexp,
        replacement,
        source,
        flags=re.MULTILINE,
    )
    assert count == 1
    _, count = re.subn(first_regexp, replacement, patched, flags=re.MULTILINE)
    assert count == 0


def test_ledger_patch_targets_the_bookkeeping_module_inline_argument() -> None:
    ledger = _task(_load_tasks(), LEDGER_TASK)
    config = ledger["ansible.builtin.replace"]
    assert config["path"].endswith("cron/scheduler_bookkeeping.py")
    regexp = config["regexp"]
    assert re.search(regexp, "            scheduler._delivery_lane_value(job, for_failure=not d.success)),\n", re.MULTILINE)
    assert not re.search(regexp, "            scheduler._delivery_lane_value(job, for_failure=not d.success or x)),\n", re.MULTILINE)
    replaced = re.sub(
        regexp,
        config["replace"],
        "            scheduler._delivery_lane_value(job, for_failure=not d.success)),\n",
        flags=re.MULTILINE,
    )
    assert 'getattr(d, "declared_failure", False)' in replaced


def test_cron_failure_routing_verifies_every_replacement() -> None:
    verify_tasks = yaml.safe_load(
        (ROLE_TASKS / "patches_verify_cron.yml").read_text()
    )
    verify = next(
        task
        for task in verify_tasks
        if task.get("name") == "Assert installed Hermes pinned-source patches (cron/memory/judge half)"
    )
    conditions = verify["ansible.builtin.assert"]["that"]
    scheduler_checks = " ".join(
        c for c in conditions if "hermes_agent_cron_scheduler_source" in c
    )
    bookkeeping_checks = " ".join(
        c for c in conditions if "hermes_agent_cron_bookkeeping_source" in c
    )
    assert "job, deliver_content, _cron_declared_failure = _cron_route" in scheduler_checks
    assert "for_failure=not d.success or _cron_declared_failure," in scheduler_checks
    assert 'job, for_failure=not d.success or _cron_declared_failure' in scheduler_checks
    assert 'for_failure=not d.success or getattr(d, "declared_failure", False)' in bookkeeping_checks


def test_preflight_accepts_only_wholly_original_or_patched_routing() -> None:
    patch_tasks = _load_tasks()
    guard = _task(
        patch_tasks,
        "Validate every cron failure-routing patch anchor before changing source",
    )["ansible.builtin.assert"]["that"]

    pristine_scheduler = (
        "def _cron_route(job, deliver_content):\n"
        "    return job, deliver_content, False\n"
        "    (\n"
        "        deliver_content, d.blocked_config, _silent_alert, d.incident_acked, d.failure_incident_id,\n"
        "    ) = _compose_run_delivery(\n"
        "        job, success=d.success, error=d.error, final_response=final_response,\n"
        "        output_file=output_file, agent_declared=d.agent_declared)\n"
        "    for_failure=not d.success,\n"
        "    d.unresolved_origin = (\n"
        '        _normalize_deliver_value(_delivery_lane_value(job, for_failure=not d.success)) == "origin"\n'
        "        and not _resolve_delivery_targets(job, for_failure=not d.success)\n"
        "    )\n"
    )
    pristine_bookkeeping = (
        "        normalized_deliver=scheduler._normalize_deliver_value(\n"
        "            scheduler._delivery_lane_value(job, for_failure=not d.success)),\n"
    )

    patched_scheduler = pristine_scheduler
    for task in patch_tasks:
        if task.get("name") not in SCHEDULER_TASKS:
            continue
        config = task["ansible.builtin.replace"]
        patched_scheduler, count = re.subn(
            config["regexp"],
            config["replace"],
            patched_scheduler,
            flags=re.MULTILINE,
        )
        assert count == 1
    ledger = _task(patch_tasks, LEDGER_TASK)["ansible.builtin.replace"]
    patched_bookkeeping, count = re.subn(
        ledger["regexp"],
        ledger["replace"],
        pristine_bookkeeping,
        flags=re.MULTILINE,
    )
    assert count == 1

    environment = Environment(autoescape=False)
    environment.filters["regex_search"] = lambda value, pattern: re.search(
        pattern, value, re.MULTILINE
    )

    def allowed(scheduler_source: str, bookkeeping_source: str) -> bool:
        context = {
            "hermes_agent_cron_failure_routing_source_text": scheduler_source,
            "hermes_agent_cron_failure_bookkeeping_source_text": bookkeeping_source,
        }
        return all(environment.compile_expression(condition)(**context) for condition in guard)

    assert allowed(pristine_scheduler, pristine_bookkeeping)
    assert allowed(patched_scheduler, patched_bookkeeping)

    call_task = _task(patch_tasks, "Route a self-declared cron failure through _cron_route")[
        "ansible.builtin.replace"
    ]
    partially_patched, count = re.subn(
        call_task["regexp"],
        call_task["replace"],
        pristine_scheduler,
        flags=re.MULTILINE,
    )
    assert count == 1
    assert not allowed(partially_patched, pristine_bookkeeping)
    assert not allowed(
        patched_scheduler.replace("d.declared_failure = _cron_declared_failure\n", ""),
        patched_bookkeeping,
    )
    assert not allowed(patched_scheduler, pristine_bookkeeping)
