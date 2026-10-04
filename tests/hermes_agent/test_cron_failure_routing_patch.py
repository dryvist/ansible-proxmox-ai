from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_TASKS = REPO_ROOT / "roles" / "hermes_agent" / "tasks"


def test_cron_failure_routing_preflights_all_anchors_before_replacing() -> None:
    patch_tasks = yaml.safe_load(
        (ROLE_TASKS / "patches_cron_failure_routing.yml").read_text()
    )
    names = [task.get("name") for task in patch_tasks]
    guard = names.index(
        "Validate every cron failure-routing patch anchor before changing source"
    )
    first_replace = names.index(
        "Route a self-declared cron failure through _cron_route"
    )
    assert guard < first_replace

    conditions = patch_tasks[guard]["ansible.builtin.assert"]["that"]
    assert len(conditions) == 4
    assert all("hermes_agent_cron_failure_routing_source_text" in c for c in conditions)
    assert all("regex_search" in c for c in conditions)

    first_regexp = patch_tasks[first_replace]["ansible.builtin.replace"]["regexp"]
    assert (
        "(?!\\n[ \\t]*job, deliver_content, _cron_declared_failure = _cron_route)"
        in first_regexp
    )
    replacement = patch_tasks[first_replace]["ansible.builtin.replace"]["replace"]
    source = (
        "    (\n"
        "        deliver_content, d.blocked_config, _silent_alert, d.incident_acked, "
        "d.failure_incident_id,\n"
        "    ) = _compose_run_delivery(\n"
        "        job, success=d.success, error=d.error, final_response=final_response,\n"
        "        output_file=output_file)\n"
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


def test_ledger_patch_matches_wrapped_or_inline_for_failure_argument() -> None:
    patch_tasks = yaml.safe_load(
        (ROLE_TASKS / "patches_cron_failure_routing.yml").read_text()
    )
    ledger = next(
        task
        for task in patch_tasks
        if task.get("name")
        == "Route the run-ledger classification through the failure lane for a declared failure"
    )
    assert r"\s*for_failure=not d\.success" in ledger["ansible.builtin.replace"]["regexp"]


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
    source_checks = " ".join(
        c for c in conditions if "hermes_agent_cron_scheduler_source" in c
    )
    assert "job, deliver_content, _cron_declared_failure = _cron_route" in source_checks
    assert "for_failure=not d.success or _cron_declared_failure," in source_checks
    assert 'for_failure=not d.success or getattr(d, "declared_failure", False)' in source_checks
    assert 'job, for_failure=not d.success or _cron_declared_failure' in source_checks
