"""The fabric watchdog include in the agents play converges on its own tag.

The watchdog is an include_role, so a tag on the include selects the include
itself and `apply` carries it into the role's tasks. `--list-tasks` on the
play pins the selection in both directions: the watchdog tag narrows the play
to the watchdog (leaving the statically imported hermes_agent role out), and
an untagged listing still carries both.
"""

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PLAY = ROOT / "playbooks/agents.yml"
WATCHDOG = "Include fabric_watchdog role"
HERMES = "Include hermes_agent role"
# import_role is static, so an untagged --list-tasks shows the role's own tasks, not the import line.
HERMES_TASKS = "hermes_agent : "


def _fixture_inventory(tmp_path):
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(yaml.safe_dump({"all": {"children": {
        group: {"hosts": {"fixture": {"ansible_connection": "local"}}}
        for group in ("hermes_agent_group", "hermes_ui_group", "herdr_group", "herdr_ui_group")
    }}}), encoding="utf-8")
    return inventory


def _list_tasks(tmp_path, *extra):
    env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / "local"))
    return subprocess.run(
        ["ansible-playbook", str(PLAY), "-i", str(_fixture_inventory(tmp_path)),
         "--list-tasks", *extra],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )


def test_the_watchdog_tag_lists_the_watchdog_and_not_the_agent_role(tmp_path):
    selected = _list_tasks(tmp_path, "--tags", "fabric_watchdog")
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert WATCHDOG in selected.stdout
    assert HERMES not in selected.stdout


def test_an_untagged_listing_still_carries_both_includes(tmp_path):
    everything = _list_tasks(tmp_path)
    assert everything.returncode == 0, everything.stdout + everything.stderr
    assert WATCHDOG in everything.stdout
    assert HERMES_TASKS in everything.stdout


def test_the_include_passes_its_tag_into_the_role():
    play = next(
        play for play in yaml.safe_load(PLAY.read_text(encoding="utf-8"))
        if play.get("hosts") == "hermes_agent_group"
    )
    include = next(
        task for task in play["tasks"][0]["block"] if task.get("name") == WATCHDOG
    )
    assert include["ansible.builtin.include_role"]["name"] == "fabric_watchdog"
    assert include["tags"] == ["fabric_watchdog"]
    assert include["ansible.builtin.include_role"]["apply"]["tags"] == ["fabric_watchdog"]
