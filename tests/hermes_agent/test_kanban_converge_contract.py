"""Source contracts for fail-closed, native Hermes Kanban converge checks."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"


def _tasks(path: str) -> list[dict]:
    return yaml.safe_load((ROLE_ROOT / "tasks" / path).read_text())


def _named(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def test_declared_bridge_bucket_names_are_nonempty_where_required_and_unambiguous():
    tasks = _tasks("assert_brain_and_bridge.yml")
    assertion = _named(
        tasks, "Assert the Hermes Kanban profile and bridge bucket declarations are valid"
    )
    conditions = assertion["ansible.builtin.assert"]["that"]

    assert any("bucket_ready | trim | length > 0" in condition for condition in conditions)
    assert any("unique | list | length" in condition for condition in conditions)
    assert assertion["vars"]["hermes_agent_vikunja_bridge_declared_bucket_names"] == [
        "{{ hermes_agent_vikunja_bridge_bucket_ready }}",
        "{{ hermes_agent_vikunja_bridge_bucket_in_progress }}",
        "{{ hermes_agent_vikunja_bridge_bucket_done }}",
        "{{ hermes_agent_vikunja_bridge_bucket_blocked }}",
        "{{ hermes_agent_vikunja_bridge_bucket_needs_triage }}",
        "{{ hermes_agent_vikunja_bridge_bucket_busy }}",
    ]


def test_board_file_check_uses_native_find_and_rejects_duplicates_or_wrong_paths():
    tasks = _tasks("mcp_precheck.yml")
    find = _named(tasks, "Find Hermes Kanban database files under the shared home")
    assertion = _named(
        tasks, "Assert Hermes uses at most one Kanban database at the configured path"
    )

    assert "ansible.builtin.find" in find
    assert find["ansible.builtin.find"]["patterns"] == "kanban.db"
    assert find["ansible.builtin.find"]["recurse"] is True
    assert find["check_mode"] is False
    conditions = assertion["ansible.builtin.assert"]["that"]
    assert any("matched | int <= 1" in condition for condition in conditions)
    assert any("difference([hermes_agent_hermes_home ~ '/kanban.db'])" in condition
               for condition in conditions)
    assert "ansible.builtin.shell" not in find and "ansible.builtin.command" not in find


def test_declared_cron_skills_are_statted_in_the_selected_home_and_missing_skills_fail():
    tasks = _tasks("mcp_precheck.yml")
    stat = _named(tasks, "Stat every declared direct-cron skill reference")
    assertion = _named(tasks, "Assert every declared direct-cron skill is installed in its job home")

    assert "ansible.builtin.stat" in stat
    assert "item.hermes_home | default(hermes_agent_hermes_home)" in stat[
        "ansible.builtin.stat"
    ]["path"]
    assert "item.skill" in stat["ansible.builtin.stat"]["path"]
    assert "SKILL.md" in stat["ansible.builtin.stat"]["path"]
    assert "selectattr('skill', 'defined')" in stat["loop"]
    assert "rejectattr('skill', 'equalto', '')" in stat["loop"]
    assert any("stat.exists" in condition and "false" in condition
               for condition in assertion["ansible.builtin.assert"]["that"])


def test_bridge_bucket_check_is_read_only_fail_closed_and_uses_the_bridge_view_route():
    tasks = _tasks("mcp_precheck.yml")
    reads = [
        _named(tasks, name)
        for name in (
            "Read Vikunja projects for the enabled Hermes bridge",
            "Read the enabled Hermes bridge project's views",
            "Read the enabled Hermes bridge Kanban view's buckets",
        )
    ]
    assert all(task["ansible.builtin.uri"]["method"] == "GET" for task in reads)
    assert all(task["no_log"] is True for task in reads)
    assert all(task["check_mode"] is False for task in reads)
    assert all(task["ansible.builtin.uri"]["follow_redirects"] == "none" for task in reads)
    assert all(task["ansible.builtin.uri"]["use_proxy"] is False for task in reads)
    assert "/views/{{ (hermes_agent_vikunja_bridge_kanban_views | first).id }}/tasks" in (
        reads[-1]["ansible.builtin.uri"]["url"]
    )

    project_assert = _named(tasks, "Assert the enabled Hermes bridge project exists exactly once")
    view_assert = _named(
        tasks, "Assert the enabled Hermes bridge project has exactly one Kanban view"
    )
    bucket_assert = _named(
        tasks, "Assert configured Hermes bridge bucket names exist in the Kanban view"
    )
    assert any("length) == 1" in condition
               for condition in project_assert["ansible.builtin.assert"]["that"])
    assert any("kanban_views | length == 1" in condition
               for condition in view_assert["ansible.builtin.assert"]["that"])
    assert any("difference(hermes_agent_vikunja_bridge_found_bucket_names)" in condition
               for condition in bucket_assert["ansible.builtin.assert"]["that"])
