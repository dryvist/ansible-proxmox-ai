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
    watching that it had gone. Its slot must also match that weekly cadence:
    a finer slot gives every enqueue a fresh key, so a re-enqueue creates a
    second card for the same week instead of resolving to the existing one,
    and the week-over-week comparison the card exists to make is keyed to a
    window it never spans.
    """
    defaults = yaml.safe_load(DEFAULTS.read_text())
    card = next(
        c for c in defaults["hermes_agent_kanban_cards"]
        if c["job"] == "{{ hermes_agent_fleet_health_cron_name }}"
    )

    assert int(card["max_retries"]) >= 3
    assert int(card["interval_hours"]) == 168
    # Weekly cron (a fixed day-of-week field), matching the 168-hour slot.
    assert defaults["hermes_agent_fleet_health_cron_schedule"].split()[-1] not in ("*", "?")


def test_slot_stamp_gives_one_key_per_period() -> None:
    """Execute the rendered `slot_stamp`, rather than pattern-match it.

    The branch selection is arithmetic on the interval, and the whole point of
    the weekly branch is that a weekly card stops getting a fresh key every day
    — a property only visible by running it.
    """
    import re
    import subprocess

    template = (
        REPO_ROOT / "roles/hermes_agent/templates/kanban-enqueue-recurring.sh.j2"
    ).read_text()
    body = re.search(r"^slot_stamp\(\) \{.*?^\}", template, re.S | re.M)
    assert body, "slot_stamp() not found — the enqueuer template changed shape"

    def stamp(interval: int) -> str:
        return subprocess.run(
            ["bash", "-c", f"{body.group(0)}\nslot_stamp {interval}\n"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    hourly, daily, weekly = stamp(1), stamp(24), stamp(168)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}", hourly), hourly
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", daily), daily
    assert re.fullmatch(r"\d{4}-W\d{2}", weekly), weekly
    assert hourly.startswith(daily)
    # The weekly key must not be a restatement of today's date, which is the
    # bug the branch exists to fix.
    assert weekly != daily
    # A sub-daily interval still buckets within the day.
    assert stamp(6).startswith(daily + "-s")
    # ISO week paired with the ISO week-numbering year, never the calendar
    # year: the two disagree around New Year (1 Jan can fall in week 52/53 of
    # the previous ISO year), so `%Y-W%V` would reuse a key already spent.
    assert "date -u +%G-W%V" in body.group(0)
    assert "%Y-W%V" not in body.group(0)
