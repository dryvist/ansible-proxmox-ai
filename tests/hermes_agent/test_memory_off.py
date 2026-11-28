"""Built-in memory is off everywhere, and old memory files move aside.

Hermes keeps memory only through the external memory service now: every config
this role renders turns both built-in switches off, and a local MEMORY.md/USER.md
an older deploy left behind is the agent's own writing, so it moves into
retired-memory/<profile>/ instead of ever being deleted.

Runs bare (``python3 tests/hermes_agent/test_memory_off.py``) or under pytest.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from test_context_limits import _render
from test_eval_profile_contract import _render_config
from test_profile_contract import ROLE_ROOT, _defaults

SWITCHES_OFF = {"memory_enabled": False, "user_profile_enabled": False}
MEMORY_FILE_NAMES = ("MEMORY.md", "USER.md")


def _profile(name: str) -> dict:
    return next(p for p in _defaults()["hermes_agent_profiles"] if p["name"] == name)


def _profile_memory(profile: dict, provider: str) -> dict:
    return _render_config(profile, provider)["memory"]


def test_a_locked_profile_with_a_bank_serves_the_fleet_memory_provider() -> None:
    """The lock itself never switched memory off — it only removed tools. With
    the built-in stores off, the declared bank is what keeps the external
    memory config profiles.yml already deploys for a locked profile in use."""
    memory = _profile_memory(_profile("youtrack-clerk"), "hindsight")
    assert memory == {"provider": "hindsight", **SWITCHES_OFF}


def test_a_locked_profile_without_a_bank_stays_builtin() -> None:
    memory = _profile_memory(_profile("semaphore-converge"), "hindsight")
    assert memory == {"provider": "builtin", **SWITCHES_OFF}


def test_a_profile_level_memory_enabled_true_no_longer_turns_the_switches_on() -> None:
    memory = _profile_memory({**_profile("splunk-admin"), "memory_enabled": True}, "hindsight")
    assert memory == {"provider": "hindsight", **SWITCHES_OFF}


def test_with_the_memory_service_off_any_profile_renders_builtin() -> None:
    memory = _profile_memory(_profile("splunk-admin"), "builtin")
    assert memory == {"provider": "builtin", **SWITCHES_OFF}


def _identity_config_memory(identity: str, *, research: bool = False) -> dict:
    """The default profile's config.yaml memory block, per branch.

    Same override shape test_pepper_research._config uses: the identity selects
    the branch (resolve_isolated derives hermes_agent_is_isolated from it), and
    the memory provider is pre-resolved the way Ansible would.
    """
    defaults = _defaults()
    _, parsed = _render(
        "config.yaml.j2", {},
        hermes_agent_id=identity,
        hermes_agent_pepper_research_enabled=research,
        hermes_agent_memory_provider="" if identity == "pepper" else "hindsight",
        hermes_agent_kanban_goal_mode=identity != "pepper",
        hermes_agent_api_server_key="fixture", hermes_agent_slack_bot_token="fixture",
        hermes_agent_api_max_concurrent_runs=1,
        hermes_agent_api_server_toolsets=defaults["hermes_agent_api_server_toolsets"],
        hermes_agent_cron_toolsets=defaults["hermes_agent_api_server_toolsets"],
        hermes_agent_slack_toolsets=defaults["hermes_agent_api_server_toolsets"] + ["clarify"],
        hermes_agent_kanban_orchestrator_profile=defaults["hermes_agent_kanban_orchestrator_profile"],
        hermes_agent_plugins_enabled=[],
        hermes_agent_kanban_goal_judge_model="fixture-judge",
    )
    return parsed["memory"]


def test_every_default_config_branch_renders_both_switches_off() -> None:
    fleet = _identity_config_memory("hermes")
    assert fleet == {"provider": "hindsight", **SWITCHES_OFF}

    pepper = _identity_config_memory("pepper")
    assert pepper == SWITCHES_OFF

    research = _identity_config_memory("pepper", research=True)
    assert research == SWITCHES_OFF


def test_no_template_turns_a_built_in_memory_switch_on() -> None:
    offenders = [
        f"{path.relative_to(ROLE_ROOT)}: {line.strip()}"
        for path in sorted(ROLE_ROOT.glob("templates/*.j2"))
        for line in path.read_text().splitlines()
        if re.search(r"\b(memory_enabled|user_profile_enabled): true\b", line)
    ]
    assert not offenders, "\n".join(offenders)


def test_the_retire_step_finds_both_memory_files_and_never_deletes_one() -> None:
    source = (ROLE_ROOT / "tasks" / "retire_memory_files.yml").read_text()
    tasks = yaml.safe_load(source)
    stat = next(t for t in tasks if t.get("ansible.builtin.stat"))
    assert stat["loop"] == list(MEMORY_FILE_NAMES)

    for task in tasks:
        command = task.get("ansible.builtin.command") or {}
        if command:
            argv = command.get("argv") or [command.get("cmd", "")]
            assert argv[0] == "mv", f"{task['name']} must move, not delete"
        file_module = task.get("ansible.builtin.file") or {}
        assert file_module.get("state") != "absent", task["name"]

    assert "rm " not in source.replace("memory", "")
    assert "state: absent" not in source


def test_the_retire_move_is_check_mode_guarded_and_dated() -> None:
    source = (ROLE_ROOT / "tasks" / "retire_memory_files.yml").read_text()
    move = next(
        t for t in yaml.safe_load(source)
        if (t.get("ansible.builtin.command") or {}).get("argv", [""])[0] == "mv"
    )
    assert move["when"] == "not ansible_check_mode"
    assert move["loop"] == "{{ hermes_agent_retire_memory_found }}"
    # A UTC date in the retired name, plus the -1, -2, ... collision suffix.
    assert "now(utc=true, fmt='%Y%m%d')" in source
    assert "namesakes | length" in source


def test_the_retire_step_is_included_once_for_the_default_and_once_per_profile() -> None:
    content = (ROLE_ROOT / "tasks" / "config_and_content.yml").read_text()
    assert content.count("ansible.builtin.include_tasks: retire_memory_files.yml") == 1
    assert 'hermes_agent_retire_memory_dir: "{{ hermes_agent_hermes_home }}"' in content
    assert 'hermes_agent_retire_memory_label: default' in content

    profiles = (ROLE_ROOT / "tasks" / "profiles.yml").read_text()
    assert profiles.count("ansible.builtin.include_tasks: retire_memory_files.yml") == 1
    assert 'hermes_agent_retire_memory_dir: "{{ hermes_agent_hermes_home }}/profiles/{{ item.name }}"' in profiles
    assert 'hermes_agent_retire_memory_label: "{{ item.name }}"' in profiles


def test_the_retire_destination_is_the_profile_memory_folder() -> None:
    source = (ROLE_ROOT / "tasks" / "retire_memory_files.yml").read_text()
    assert "{{ hermes_agent_retire_memory_dir }}/{{ item }}" in source
    assert "{{ hermes_agent_hermes_home }}/retired-memory/{{ hermes_agent_retire_memory_label }}" in source
    # The service user owns the retired copies.
    assert 'owner: "{{ hermes_agent_user }}"' in source


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
