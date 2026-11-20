"""An identity that runs no profiles moves leftover profile directories out of Hermes's scan path."""

from __future__ import annotations

from pathlib import Path

import yaml

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
MAIN = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
TASKS = yaml.safe_load((ROLE / "tasks/skills_and_profiles.yml").read_text())
DEFAULTS = yaml.safe_load((ROLE / "defaults/main/150-fleet-health-profiles.yml").read_text())


def _task(tasks: list, name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def test_only_the_profile_guard_turns_retirement_on():
    assert DEFAULTS["hermes_agent_profiles_forced_off"] is False
    guard = _task(MAIN, "Keep operational profiles off the private identity")
    assert guard["ansible.builtin.set_fact"] == {"hermes_agent_profiles": [], "hermes_agent_profiles_forced_off": True}
    # The key-gate filter may also empty the list, but must never retire a profile.
    gate = _task(MAIN, "Keep key-gated profiles off until their router key is fetched")
    assert "hermes_agent_profiles_forced_off" not in gate["ansible.builtin.set_fact"]


def test_leftover_profiles_move_and_are_never_deleted():
    names = [task.get("name") for task in TASKS]
    loop_at = names.index("Reconcile named Hermes operating profiles")
    find_at = names.index("Find leftover profile directories on an identity that runs no profiles")
    move_at = names.index("Move leftover profile directories out of the profile scan")
    assert loop_at < find_at < move_at
    find = TASKS[find_at]
    assert find["when"] == "hermes_agent_profiles_forced_off | bool"
    assert find["ansible.builtin.find"]["recurse"] is False
    move = TASKS[move_at]
    assert move["when"] == "hermes_agent_profiles_forced_off | bool"
    argv = move["ansible.builtin.command"]["argv"]
    assert argv[:3] == ["mv", "--no-target-directory", "{{ item.path }}"]
    assert argv[3].startswith("{{ hermes_agent_retired_profiles_dir }}/")
    assert move["ansible.builtin.command"]["removes"] == "{{ item.path }}"
    assert DEFAULTS["hermes_agent_retired_profiles_dir"] == "{{ hermes_agent_hermes_home }}/retired-profiles"
    assert not any(
        (task.get("ansible.builtin.file") or {}).get("state") == "absent"
        and "profiles" in str((task.get("ansible.builtin.file") or {}).get("path", ""))
        for task in TASKS
    )
