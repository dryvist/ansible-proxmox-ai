"""The managed stop gate must not start Hindsight or destroy its data."""

from pathlib import Path

import jinja2
import pytest
import yaml

from _compose_render import INVENTORY_DEFAULTS, ROLE_DEFAULTS, render


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/hindsight_docker"


def _tasks(name):
    return yaml.safe_load((ROLE / "tasks" / name).read_text())


def _condition(expression, **values):
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["bool"] = lambda value: (
        value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}
    )
    return bool(env.compile_expression(expression)(**values))


def test_normal_containment_neither_renders_nor_requires_secrets():
    main = _tasks("main.yml")
    contain = next(task for task in main if task.get("ansible.builtin.import_tasks") == "contain.yml")
    deploy = next(task for task in main if task.get("ansible.builtin.import_tasks") == "deploy.yml")
    assert yaml.safe_load(jinja2.Environment().from_string(
        INVENTORY_DEFAULTS["hindsight_service_enabled"]
    ).render()) is False
    assert ROLE_DEFAULTS["hindsight_docker_migration_approved_image"] == ""
    for enabled, approved, contains, deploys in (
        (False, False, True, False),
        (False, True, True, True),
        (True, False, False, True),
        (True, True, False, True),
    ):
        values = dict(hindsight_service_enabled=enabled, hindsight_docker_migration_approved_image="target" if approved else "")
        assert _condition(contain["when"], **values) is contains
        assert _condition(deploy["when"], **values) is deploys
    assert _condition(contain["when"]) is True
    assert _condition(deploy["when"], hindsight_docker_migration_approved_image="") is False
    deploy_tasks = _tasks("deploy.yml")
    assert any("ansible.builtin.template" in task for task in deploy_tasks)
    assert any("ansible.builtin.assert" in task for task in deploy_tasks)


@pytest.mark.parametrize("state,enabled", [(True, True), (False, False), ("false", False), ("true", False), (None, False)])
def test_published_service_state_requires_a_real_boolean(state, enabled):
    template = jinja2.Environment().from_string(INVENTORY_DEFAULTS["hindsight_service_enabled"])
    rendered = template.render(tofu_data={"constants": {"service_state": {"hindsight_enabled": state}}})
    assert yaml.safe_load(rendered) is enabled


def test_stop_preserves_cache_and_never_runs_compose_down():
    tasks = _tasks("contain.yml")
    stop = next(task for task in tasks if "community.docker.docker_compose_v2" in task)
    assert stop["community.docker.docker_compose_v2"]["state"] == "stopped"
    assert stop["when"] == "hindsight_docker_contain_compose.stat.exists"
    assert all("--volumes" not in str(task) and " down" not in str(task) for task in tasks)
    assert all(
        marker not in str(task)
        for task in tasks
        for marker in ("psql", "hindsight-admin", "ansible.builtin.template", "fetch_s3_secret")
    )
    assert any(
        task.get("ansible.builtin.command", {}).get("argv", [])[:2] == ["docker", "ps"]
        for task in tasks
        if isinstance(task.get("ansible.builtin.command"), dict)
    )
    assert any("ansible.builtin.assert" in task for task in tasks)


def test_staging_cannot_start_the_service_and_handler_obeys_the_same_gate():
    deploy = _tasks("deploy.yml")
    live = next(task for task in deploy if task.get("community.docker.docker_compose_v2", {}).get("state") == "present")
    stage = next(task for task in deploy if task.get("ansible.builtin.import_tasks") == "stage_stopped.yml")
    for enabled in (False, True):
        assert _condition(live["when"], hindsight_service_enabled=enabled) is enabled
        assert _condition(stage["when"], hindsight_service_enabled=enabled) is (not enabled)
    stage_tasks = _tasks("stage_stopped.yml")
    assert any("create" in str(task.get("ansible.builtin.command", {})) for task in stage_tasks)
    assert any(task.get("ansible.builtin.import_tasks") == "contain.yml" for task in stage_tasks)
    handlers = yaml.safe_load((ROLE / "handlers/main.yml").read_text())
    handler = next(task for task in handlers if task["name"] == "Restart hindsight")
    expression = handler["community.docker.docker_compose_v2"]["state"]
    env = jinja2.Environment()
    env.filters["bool"] = bool
    assert env.from_string(expression).render(hindsight_service_enabled=False) == "stopped"
    assert env.from_string(expression).render(hindsight_service_enabled=True) == "present"


@pytest.mark.parametrize("enabled,restart", [(False, "no"), (True, "unless-stopped")])
def test_rendered_restart_policy_tracks_containment(enabled, restart):
    compose = yaml.safe_load(render({"hindsight_service_enabled": enabled}, strict=True))
    assert compose["services"]["hindsight"]["restart"] == restart
    assert compose["services"]["hindsight"]["volumes"] == ["hindsight-cache:/home/hindsight/.cache"]


def test_rendered_worker_and_inference_defaults_are_bounded_per_process():
    env = yaml.safe_load(render(strict=True))["services"]["hindsight"]["environment"]
    assert {
        key: env[key]
        for key in (
            "HINDSIGHT_API_WORKERS",
            "HINDSIGHT_API_WORKER_ENABLED",
            "HINDSIGHT_API_WORKER_MAX_SLOTS",
            "HINDSIGHT_API_WORKER_CONSOLIDATION_RESERVED_SLOTS",
            "HINDSIGHT_API_LLM_MAX_CONCURRENT",
            "HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT",
            "HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION",
            "HINDSIGHT_API_CONSOLIDATION_RECONCILE_INTERVAL_SECONDS",
            "HINDSIGHT_API_MENTAL_MODEL_REFRESH_TICK_SECONDS",
            "HINDSIGHT_API_EMBEDDINGS_LOCAL_FORCE_CPU",
            "HINDSIGHT_API_RERANKER_LOCAL_FORCE_CPU",
            "HF_HUB_OFFLINE",
        )
    } == {
        "HINDSIGHT_API_WORKERS": "1",
        "HINDSIGHT_API_WORKER_ENABLED": "true",
        "HINDSIGHT_API_WORKER_MAX_SLOTS": "1",
        "HINDSIGHT_API_WORKER_CONSOLIDATION_RESERVED_SLOTS": "0",
        "HINDSIGHT_API_LLM_MAX_CONCURRENT": "1",
        "HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT": "1",
        "HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION": "false",
        "HINDSIGHT_API_CONSOLIDATION_RECONCILE_INTERVAL_SECONDS": "0",
        "HINDSIGHT_API_MENTAL_MODEL_REFRESH_TICK_SECONDS": "0",
        "HINDSIGHT_API_EMBEDDINGS_LOCAL_FORCE_CPU": "true",
        "HINDSIGHT_API_RERANKER_LOCAL_FORCE_CPU": "true",
        "HF_HUB_OFFLINE": "1",
    }


def test_all_replicas_and_dr_jobs_stop_before_serial_migration():
    plays = yaml.safe_load((ROOT / "playbooks/hindsight.yml").read_text())
    stopped = next(index for index, play in enumerate(plays) if play["name"].startswith("Stop every Hindsight"))
    service = next(index for index, play in enumerate(plays) if play["name"] == "Configure Hindsight agent-memory service")
    preplay = plays[stopped]
    assert stopped < service
    assert preplay["hosts"] == "hindsight_group"
    assert "serial" not in preplay
    assert preplay["any_errors_fatal"] is True
    assert not preplay.get("ignore_unreachable", False)
    includes = [task for task in preplay["tasks"] if "ansible.builtin.include_role" in task]
    assert [task["ansible.builtin.include_role"]["tasks_from"] for task in includes] == ["maintenance_preflight.yml", "stop.yml", "contain.yml"]
    for task in includes:
        for enabled, approved in ((False, False), (False, True), (True, True), (True, False)):
            assert _condition(
                task["when"],
                hindsight_service_enabled=enabled,
                hindsight_docker_migration_approved_image="target" if approved else "",
            ) is approved
