"""Contract tests for the tracker agents' memory bank settings (role defaults and task file)."""

from __future__ import annotations

from pathlib import Path
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "hermes_agent"))
from _shell_harness_shared import defaults_of  # noqa: E402

DEFAULTS = defaults_of("hermes_agent")
AGENTS = [
    "vikunja-clerk",
    "vikunja-triage",
    "vikunja-nightly",
    "vikunja-windows",
    "youtrack-clerk",
    "zammad-clerk",
]
TASKS = REPO_ROOT / "roles/hermes_agent/tasks"


def test_one_bank_row_per_tracker_agent() -> None:
    rows = DEFAULTS["hermes_agent_tracker_memory_banks"]
    assert [row["agent"] for row in rows] == AGENTS
    for row in rows:
        assert set(row) == {"agent", "bank_id", "updates"}
        assert row["agent"] in row["bank_id"]
        assert "hermes_agent_memory_bank_id" in row["bank_id"]


def test_every_bank_keeps_conventions_only_and_two_tools() -> None:
    mission = DEFAULTS["hermes_agent_tracker_memory_retain_mission"]
    assert "conventions" in mission
    assert "Never" in mission
    for row in DEFAULTS["hermes_agent_tracker_memory_banks"]:
        updates = row["updates"]
        assert set(updates) == {"retain_mission", "mcp_enabled_tools"}
        assert updates["mcp_enabled_tools"] == ["recall", "retain"]
        assert "hermes_agent_tracker_memory_retain_mission" in updates["retain_mission"]


def test_bank_settings_are_off_by_default_and_looped_from_one_file() -> None:
    assert DEFAULTS["hermes_agent_tracker_memory_banks_enabled"] is False
    main = (TASKS / "config_and_content.yml").read_text(encoding="utf-8")
    assert "tracker_memory_bank.yml" in main
    assert "hermes_agent_tracker_memory_banks_enabled" in main
    top = yaml.safe_load((TASKS / "tracker_memory_bank.yml").read_text(encoding="utf-8"))
    steps = [inner for step in top for inner in step.get("block", [step])]
    uri_steps = [step for step in steps if "ansible.builtin.uri" in step]
    # Read the bank, create it when missing, then read, set and read back its settings.
    assert [step["ansible.builtin.uri"]["method"] for step in uri_steps] == ["GET", "PUT", "GET", "PATCH", "GET"]
    create, patch = uri_steps[1], uri_steps[3]
    assert "status == 404" in str(create["when"]) and "not ansible_check_mode" in str(create["when"])
    assert "[item for" not in str(steps), "Jinja has no list comprehension"
    assert "not ansible_check_mode" in str(patch["when"])
    assert "overrides" in str(patch["when"])
    assert all(step.get("no_log") is True for step in uri_steps)
    assert any("ansible.builtin.assert" in step for step in steps)


def test_splunk_bank_block_is_unchanged() -> None:
    main = (TASKS / "config_and_content.yml").read_text(encoding="utf-8")
    assert "Set the Splunk memory bank extraction mode" in main
    assert DEFAULTS["hermes_agent_splunk_memory_retain_extraction_mode"] == "chunks"
