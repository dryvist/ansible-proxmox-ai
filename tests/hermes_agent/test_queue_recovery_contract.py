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
    assert "retries: 5" in playbook
    assert "until: >-" in playbook
    assert "kanban gc" not in playbook
    assert "archive {{ item }} --rm" not in BOARD_TASKS.read_text()


def test_queue_recovery_archives_each_board_before_reconciling() -> None:
    tasks = BOARD_TASKS.read_text()

    assert "kanban --board {{ hermes_queue_recovery_board.slug }} list --json" in tasks
    assert "kanban --board {{ hermes_queue_recovery_board.slug }} archive" in tasks
    assert "hermes_queue_recovery_task_ids | join(' ')" in tasks
    assert "Assert no active tasks remain" in tasks


def test_heartbeat_is_limited_to_waking_hours() -> None:
    """Hourly, and only while the operator is awake.

    Asserts the HOUR field, not the whole expression. It used to pin the exact
    string "0 8-22 * * *", which made the minute part of a contract that is
    about waking hours — so moving the card off minute :00 to stop it colliding
    with the */15 board digest every hour failed a test named for something
    else. The minute is deliberately free to change; where it may land is the
    separate concern owned by test_cron_stagger.py.
    """
    defaults = yaml.safe_load(DEFAULTS.read_text())
    minute, hours, dom, month, dow = defaults["hermes_agent_daily_status_cron_schedule"].split()

    assert hours == "8-22", "the heartbeat must stay inside waking hours"
    assert (dom, month, dow) == ("*", "*", "*"), "the heartbeat runs every day"
    assert minute.isdigit(), f"the heartbeat runs once per hour, not on {minute!r}"


def test_watchdog_reports_command_outcomes_not_only_desired_count() -> None:
    watchdog = WATCHDOG.read_text()

    assert "CRON_SUCCEEDED=0" in watchdog
    assert "CRON_FAILED=0" in watchdog
    assert "succeeded=${CRON_SUCCEEDED} failed=${CRON_FAILED}" in watchdog
    assert "does not verify Kanban queue health" in watchdog


def test_no_card_is_retired_by_its_first_failure() -> None:
    """`max_retries` is a failure LIMIT, so 1 means zero retries.

    A recurring card has no other recovery path: nothing re-attempts a blocked
    card, it simply waits for its next slot, which for the weekly cards is a
    week with that workload uncovered. Duplicated as a converge assert in
    assert.yml — this is the copy that runs without a guest.
    """
    cards = yaml.safe_load(DEFAULTS.read_text())["hermes_agent_kanban_cards"]

    offenders = [c["job"] for c in cards if int(c["max_retries"]) < 2]
    assert offenders == [], f"cards blocked by their first failure: {offenders}"


def test_fleet_health_survives_a_lost_run_and_keys_on_its_own_cadence() -> None:
    """The card that watches the other cards must outlive one bad run.

    It is the fleet's only regression check and it fires weekly, so a single
    crash or wall-clock kill used to cost a full week of cover with nothing
    watching that it had gone. Its idempotency key is now stable (the job
    name, not a slot — see reconcile_kanban_card_cron.yml), so a re-enqueue in
    the same week resolves to the existing card either way; what still has to
    hold is that the crontab schedule is genuinely weekly, matching the
    week-over-week comparison the card exists to make.
    """
    defaults = yaml.safe_load(DEFAULTS.read_text())
    card = next(
        c for c in defaults["hermes_agent_kanban_cards"]
        if c["job"] == "{{ hermes_agent_fleet_health_cron_name }}"
    )

    assert int(card["max_retries"]) >= 3
    assert int(card["interval_hours"]) == 168
    # Weekly cron (a fixed day-of-week field), matching the 168-hour cadence.
    assert defaults["hermes_agent_fleet_health_cron_schedule"].split()[-1] not in ("*", "?")

# test_slot_stamp_gives_one_key_per_period was removed with the native-cron
# redesign: slot_stamp() no longer exists anywhere (idempotency keys are
# stable per job, not slotted) — see reconcile_kanban_card_cron.yml.
