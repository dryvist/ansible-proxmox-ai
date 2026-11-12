"""Exercise the Hermes Kanban converge checks against rendered task conditions."""

from pathlib import Path

import yaml
from jinja2 import Environment

from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
ENV = Environment(autoescape=False)
ENV.filters["bool"] = lambda value: (
    value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "1")
)


def _tasks(path: str) -> list[dict]:
    return yaml.safe_load((ROLE_ROOT / "tasks" / path).read_text())


def _named(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _conditions_pass(conditions: list[str] | str, context: dict) -> bool:
    if isinstance(conditions, str):
        conditions = [conditions]
    results = [ENV.compile_expression(str(condition))(**context) for condition in conditions]
    assert all(isinstance(result, bool) for result in results), results
    return all(results)


def _task_runs(task: dict, context: dict) -> bool:
    return _conditions_pass(task.get("when", []), context)


def _direct_job_wanted(job: dict, identity: str) -> bool:
    expression = role_defaults(ROLE_ROOT)["hermes_agent_direct_job_wanted"]
    rendered = ENV.from_string(expression).render(
        item=job,
        hermes_agent_id=identity,
        hermes_agent_job_identities=job.get("identities", [identity]),
    )
    return rendered.lower() == "true"


def _declared_bucket_context(assertion: dict, values: dict) -> dict:
    declared = assertion["vars"]["hermes_agent_vikunja_bridge_declared_bucket_names"]
    values = dict(values)
    values["hermes_agent_vikunja_bridge_declared_bucket_names"] = [
        ENV.from_string(name).render(**values) for name in declared
    ]
    return values


def test_optional_declared_buckets_may_be_empty_but_names_stay_distinct():
    assertion = _named(
        _tasks("assert_brain_and_bridge.yml"),
        "Assert the Hermes Kanban profile and bridge bucket declarations are valid",
    )
    base = {
        "hermes_agent_id": "hermes",
        "hermes_agent_kanban_orchestrator_profile": "orchestrator",
        "hermes_agent_profiles": [{"name": "orchestrator"}],
        "hermes_agent_vikunja_bridge_bucket_ready": "Ready",
        "hermes_agent_vikunja_bridge_bucket_in_progress": "",
        "hermes_agent_vikunja_bridge_bucket_done": "",
        "hermes_agent_vikunja_bridge_bucket_blocked": "",
        "hermes_agent_vikunja_bridge_bucket_needs_triage": "",
        "hermes_agent_vikunja_bridge_bucket_busy": "",
    }
    assert _conditions_pass(
        assertion["ansible.builtin.assert"]["that"],
        _declared_bucket_context(assertion, base),
    )

    duplicate = {
        **base,
        "hermes_agent_vikunja_bridge_bucket_done": "Done",
        "hermes_agent_vikunja_bridge_bucket_busy": "Done",
    }
    assert not _conditions_pass(
        assertion["ansible.builtin.assert"]["that"],
        _declared_bucket_context(assertion, duplicate),
    )


def test_orchestrator_membership_only_applies_to_materialized_profiles():
    assertion = _named(
        _tasks("assert_brain_and_bridge.yml"),
        "Assert the Hermes Kanban profile and bridge bucket declarations are valid",
    )
    conditions = assertion["ansible.builtin.assert"]["that"]
    base = {
        "hermes_agent_kanban_orchestrator_profile": "orchestrator",
        "hermes_agent_vikunja_bridge_bucket_ready": "Ready",
        "hermes_agent_vikunja_bridge_bucket_in_progress": "",
        "hermes_agent_vikunja_bridge_bucket_done": "",
        "hermes_agent_vikunja_bridge_bucket_blocked": "",
        "hermes_agent_vikunja_bridge_bucket_needs_triage": "",
        "hermes_agent_vikunja_bridge_bucket_busy": "",
    }

    pepper = {
        **base,
        "hermes_agent_id": "pepper",
        "hermes_agent_profiles": [],
    }
    assert not _task_runs(assertion, pepper)

    hermes_private = {
        **base,
        "hermes_agent_id": "hermes-private",
        "hermes_agent_profiles": [],
    }
    private_context = _declared_bucket_context(assertion, hermes_private)
    assert _task_runs(assertion, hermes_private)
    assert _conditions_pass([conditions[0]], private_context)
    assert _conditions_pass(conditions[1:], private_context)

    normal_missing = {
        **base,
        "hermes_agent_id": "hermes",
        "hermes_agent_profiles": [{"name": "worker"}],
    }
    assert not _conditions_pass(
        conditions,
        _declared_bucket_context(assertion, normal_missing),
    )

    normal_present = {
        **base,
        "hermes_agent_id": "hermes",
        "hermes_agent_profiles": [{"name": "orchestrator"}],
    }
    assert _conditions_pass(
        conditions,
        _declared_bucket_context(assertion, normal_present),
    )


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


def test_direct_cron_skill_precheck_uses_reconcile_gates_and_existing_homes():
    tasks = _tasks("mcp_precheck.yml")
    home_stat = _named(tasks, "Stat the Hermes home for each direct-cron skill reference")
    skill_stat = _named(tasks, "Stat every declared direct-cron skill reference")
    assertion = _named(tasks, "Assert every declared direct-cron skill is installed in its job home")

    base_job = {
        "name": "profile-skill-job",
        "enabled": True,
        "identities": ["hermes"],
        "skill": "dryvist/example",
        "hermes_home": "/home/hermes/.hermes/profiles/github-maint",
    }
    path_context = {
        "item": base_job,
        "hermes_agent_hermes_home": "/home/hermes/.hermes",
    }
    assert ENV.from_string(home_stat["ansible.builtin.stat"]["path"]).render(
        **path_context
    ) == base_job["hermes_home"]
    assert ENV.from_string(skill_stat["ansible.builtin.stat"]["path"]).render(
        **path_context
    ).strip() == base_job["hermes_home"] + "/skills/dryvist/example/SKILL.md"
    cases = (
        (base_job, "hermes", ["/home/hermes/.hermes", base_job["hermes_home"]], True),
        ({**base_job, "enabled": False}, "hermes",
         ["/home/hermes/.hermes", base_job["hermes_home"]], False),
        ({**base_job, "identities": ["donna"]}, "hermes",
         ["/home/hermes/.hermes", base_job["hermes_home"]], False),
        (base_job, "hermes", ["/home/hermes/.hermes"], False),
    )
    for job, identity, materialized_homes, expected in cases:
        context = {
            "item": job,
            "hermes_agent_hermes_home": "/home/hermes/.hermes",
            "hermes_agent_plugin_homes": materialized_homes,
            "hermes_agent_direct_job_wanted": _direct_job_wanted(job, identity),
        }
        assert _task_runs(home_stat, context) is expected, job

    home_results = [
        {"item": base_job, "stat": {"exists": True, "isdir": True}},
        {"item": {**base_job, "name": "missing-home"},
         "stat": {"exists": False, "isdir": False}},
    ]
    loop = skill_stat["loop"].strip()[2:-2].strip()
    selected_jobs = ENV.compile_expression(loop)(
        hermes_agent_direct_cron_skill_homes={"results": home_results}
    )
    assert [job["name"] for job in selected_jobs] == ["profile-skill-job"]

    assert not _task_runs(
        assertion, {"hermes_agent_direct_cron_skill_stats": {"results": []}}
    )
    assert _task_runs(
        assertion,
        {"hermes_agent_direct_cron_skill_stats": {"results": [{"stat": {"exists": True}}]}},
    )
    missing_skill = {
        "hermes_agent_direct_cron_skill_stats": {
            "results": [{
                "item": {"name": "missing-skill"},
                "stat": {"exists": False},
            }],
        },
    }
    assert not _conditions_pass(assertion["ansible.builtin.assert"]["that"], missing_skill)


def test_bridge_requires_ready_only_while_preserving_read_only_probes():
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

    bucket_assert = _named(
        tasks, "Assert configured Hermes bridge bucket names exist in the Kanban view"
    )
    conditions = bucket_assert["ansible.builtin.assert"]["that"]
    assert _conditions_pass(
        conditions,
        {
            "hermes_agent_vikunja_bridge_bucket_ready": "Ready",
            "hermes_agent_vikunja_bridge_found_bucket_names": ["Ready"],
        },
    )
    assert not _conditions_pass(
        conditions,
        {
            "hermes_agent_vikunja_bridge_bucket_ready": "Ready",
            "hermes_agent_vikunja_bridge_found_bucket_names": ["In Progress", "Done"],
        },
    )

    project_assert = _named(tasks, "Assert the enabled Hermes bridge project exists exactly once")
    view_assert = _named(
        tasks, "Assert the enabled Hermes bridge project has exactly one Kanban view"
    )
    assert any("length) == 1" in condition
               for condition in project_assert["ansible.builtin.assert"]["that"])
    assert any("kanban_views | length == 1" in condition
               for condition in view_assert["ansible.builtin.assert"]["that"])
