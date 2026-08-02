"""Pins the concrete, mechanically-checkable claims from the 2026-08-01 kanban audit.

Four changes, each with a specific way to regress silently:

1. The splunk-digest kanban card is REMOVED (not paused). Its old memory key
   ("splunk-digest-last") was recalled by hermes-splunk-triage's catalog
   prompt as a dangling read once nothing wrote it any more — fixed in
   dryvist/ai-llm-prompts, guarded here at converge time too (assert.yml).
   A re-add of the card (or its vars) without also re-checking that prompt
   would reopen the same trap.
2. splunk-parsing-quality-v2 (direct cron) is retired in favour of the
   splunk-parsing kanban card — a 1-for-1 swap on the same daily cadence, not
   a throughput increase — because its fixed SPL queried the proven
   stale/near-empty `index=network`. test_retired_direct_crons.py already
   proves the generic replaced_by_card shape; this pins the specific pairing.
3. The new fleet-health card exists, is assigned to the default profile
   (cross-domain/meta, same home as review/daily-summary), and starts paused
   like every new card.
4. daily-summary's prompt defers to daily-operator-summary-v2's saved memory
   key for Splunk ingest volume instead of re-deriving it, so unpausing
   daily-summary later does not double the Splunk tool calls that topic costs.

Runs bare (`python3 tests/hermes_agent/test_kanban_audit_20260801.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS_PATH = REPO_ROOT / "roles/hermes_agent/defaults/main.yml"
TASKS_PATH = REPO_ROOT / "roles/hermes_agent/tasks/main.yml"


def _defaults():
    return yaml.safe_load(DEFAULTS_PATH.read_text())


def _cards():
    return {c["job"]: c for c in _defaults()["hermes_agent_kanban_cards"]}


def _direct_crons():
    return {j["name"]: j for j in _defaults()["hermes_agent_direct_cron_jobs"]}


def test_splunk_digest_card_is_fully_removed() -> None:
    defaults = _defaults()
    jobs = {c["job"] for c in defaults["hermes_agent_kanban_cards"]}
    assert "{{ hermes_agent_splunk_digest_cron_name }}" not in jobs
    assert "{{ hermes_agent_splunk_digest_cron_name }}" not in defaults["hermes_agent_kanban_paused_jobs"]
    assert "hermes_agent_splunk_digest_cron_name" not in defaults
    assert "hermes_agent_splunk_digest_cron_schedule" not in defaults
    assert "hermes_agent_splunk_digest_cron_prompt_file" not in defaults
    # The prompt-loading set_fact for the removed card must be gone too, or a
    # converge quietly keeps building an unused catalog lookup.
    tasks = TASKS_PATH.read_text()
    assert "hermes_agent_splunk_digest_cron_prompt" not in tasks


def test_splunk_triage_prompt_regression_guard_exists() -> None:
    # The converge-time promotion of the dangling-memory-key check. Lives in
    # tasks/main.yml (not assert.yml): it checks the RENDERED catalog prompt
    # text, which only exists after the "Load active Hermes prompts" set_fact
    # runs, later than assert.yml's Layer-1 checks. Structural: proves the
    # guard task exists and names the right var and dead key, not that it
    # currently passes (that needs the rendered catalog prompt, which this
    # repo does not fetch).
    tasks_text = TASKS_PATH.read_text()
    assert "Assert the Splunk triage prompt does not recall a dead job's memory key" in tasks_text
    assert "hermes_agent_splunk_triage_cron_prompt" in tasks_text
    assert "splunk-digest-last" in tasks_text


def test_splunk_parsing_quality_v2_is_retired_in_favour_of_the_kanban_card() -> None:
    direct = _direct_crons()
    job = direct["splunk-parsing-quality-v2"]
    assert job["enabled"] is False
    assert job["replaced_by_card"] == "{{ hermes_agent_splunk_parsing_cron_name }}"

    cards = _cards()
    assert "{{ hermes_agent_splunk_parsing_cron_name }}" in cards
    defaults = _defaults()
    assert "{{ hermes_agent_splunk_parsing_cron_name }}" not in defaults["hermes_agent_kanban_paused_jobs"], (
        "splunk-parsing-quality-v2 is retired in its favour, so the card "
        "must not be paused or the topic goes unreported by both paths"
    )

    # The retirement's pause task (disable-don't-delete) is wired the same
    # way as every other direct-cron retirement in this file.
    tasks = TASKS_PATH.read_text()
    assert "hermes_agent_retire_splunk_parsing_v2" in tasks
    assert "cron pause {{ hermes_agent_retired_splunk_parsing_v2_cron_name }}" in tasks
    defaults = _defaults()
    assert defaults["hermes_agent_retired_splunk_parsing_v2_cron_name"] == "splunk-parsing-quality-v2"


def test_fleet_health_card_exists_and_starts_paused() -> None:
    cards = _cards()
    card = cards.get("{{ hermes_agent_fleet_health_cron_name }}")
    assert card is not None, "fleet-health card is missing from hermes_agent_kanban_cards"
    assert card["assignee"] == "", "fleet-health is cross-domain/meta — belongs on the default profile"
    assert card["skills"] == []

    defaults = _defaults()
    assert "{{ hermes_agent_fleet_health_cron_name }}" in defaults["hermes_agent_kanban_paused_jobs"], (
        "a brand-new card starts paused like every other one added under the throughput throttle"
    )
    assert defaults["hermes_agent_fleet_health_cron_name"] == "fleet-health"

    prompt = defaults["hermes_agent_fleet_health_cron_prompt"]
    assert "kanban runs" in prompt
    assert "fleet-health-last" in prompt
    # Must never prescribe touching serving infrastructure — the whole point
    # is a read-only reliability signal, not a remediation action.
    assert "restart" in prompt and "never" in prompt


def test_daily_summary_defers_to_daily_operator_summary_v2_for_ingest_volume() -> None:
    prompt = _defaults()["hermes_agent_summary_cron_prompt"]
    assert "daily-operator-summary-last" in prompt


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
