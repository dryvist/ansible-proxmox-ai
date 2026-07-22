from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "playbooks" / "recover-hermes-queue.yml"
BOARD_TASKS = REPO_ROOT / "playbooks" / "tasks" / "recover-hermes-queue-board.yml"
WATCHDOG = REPO_ROOT / "roles" / "hermes_agent" / "templates" / "hermes-brain-watchdog.sh.j2"
DEFAULTS = REPO_ROOT / "roles" / "hermes_agent" / "defaults" / "main.yml"


def test_queue_recovery_requires_confirmation_and_uses_native_archival() -> None:
    playbook = PLAYBOOK.read_text()

    assert "hermes_queue_recovery_confirm: false" in playbook
    assert "../roles/codex_runner/defaults/main.yml" in playbook
    assert "kanban boards list --json" in playbook
    assert "include_tasks: tasks/recover-hermes-queue-board.yml" in playbook
    assert "include_role:\n        name: codex_runner" in playbook
    assert "include_role:\n        name: hermes_agent" in playbook
    assert "cron remove {{ item.id }}" in playbook
    assert "Clear stale Hermes cron desired-state markers" in playbook
    assert "hermes_queue_recovery_expected_cron_names" in playbook
    assert "kanban gc" not in playbook
    assert "archive {{ item }} --rm" not in BOARD_TASKS.read_text()


def test_queue_recovery_archives_each_board_before_reconciling() -> None:
    tasks = BOARD_TASKS.read_text()

    assert "kanban --board {{ hermes_queue_recovery_board.slug }} list --json" in tasks
    assert "kanban --board {{ hermes_queue_recovery_board.slug }} archive" in tasks
    assert "hermes_queue_recovery_task_ids | join(' ')" in tasks
    assert "Assert no active tasks remain" in tasks


def test_heartbeat_is_limited_to_waking_hours() -> None:
    defaults = yaml.safe_load(DEFAULTS.read_text())

    assert defaults["hermes_agent_daily_status_cron_schedule"] == "0 8-22 * * *"


def test_watchdog_reports_command_outcomes_not_only_desired_count() -> None:
    watchdog = WATCHDOG.read_text()

    assert "CRON_SUCCEEDED=0" in watchdog
    assert "CRON_FAILED=0" in watchdog
    assert "succeeded=${CRON_SUCCEEDED} failed=${CRON_FAILED}" in watchdog
    assert "does not verify Kanban queue health" in watchdog
