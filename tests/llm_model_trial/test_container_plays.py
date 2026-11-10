from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/llm_model_trial"
TASKS = ROLE / "tasks"
MODE = "hostvars['localhost'].llm_model_trial_container_settings.container_mode | bool"


def load(name: str) -> list[dict[str, Any]]:
    return yaml.safe_load((TASKS / name).read_text())


def names(tasks: list[dict[str, Any]]) -> list[str]:
    return [task.get("name", "") for task in tasks]


def include_of(task: dict[str, Any]) -> str:
    return task["ansible.builtin.include_tasks"]


def test_playbook_runs_prestage_suspend_serve_and_restore_plays_in_order() -> None:
    plays = yaml.safe_load((ROOT / "playbooks/llm-model-trial.yml").read_text())
    assert [play.get("hosts") for play in plays[1:]] == [
        "localhost",
        "llm_model_trial_candidate_group",
        "llm_gpu_group",
        "llm_model_trial_candidate_group",
        "llm_gpu_group",
    ]
    for play in plays[2:]:
        assert play["any_errors_fatal"] is True


def test_candidate_group_is_populated_on_localhost_from_the_preflight_host() -> None:
    localhost = yaml.safe_load((ROOT / "playbooks/llm-model-trial.yml").read_text())[1]
    add_host = next(task for task in localhost["tasks"] if "ansible.builtin.add_host" in task)
    assert add_host["ansible.builtin.add_host"]["groups"] == "llm_model_trial_candidate_group"
    assert "llm_model_trial_candidate_host" in add_host["ansible.builtin.add_host"]["name"]


def test_controller_preflight_and_candidate_placement_run_under_the_trial_tag() -> None:
    localhost = yaml.safe_load((ROOT / "playbooks/llm-model-trial.yml").read_text())[1]
    preflight = next(task for task in localhost["tasks"] if "ansible.builtin.include_role" in task)
    add_host = next(task for task in localhost["tasks"] if "ansible.builtin.add_host" in task)
    assert preflight["tags"] == ["llm_model_trial"]
    assert preflight["ansible.builtin.include_role"]["apply"]["tags"] == ["llm_model_trial"]
    assert add_host["tags"] == ["llm_model_trial"]


def test_default_and_container_candidate_flows_are_mutually_exclusive() -> None:
    plays = yaml.safe_load((ROOT / "playbooks/llm-model-trial.yml").read_text())
    candidate = plays[4]
    default_task, container_task = candidate["tasks"]
    assert default_task["ansible.builtin.include_role"]["tasks_from"] == "serve.yml"
    assert default_task["when"] == f"not ({MODE})"
    assert container_task["ansible.builtin.include_role"]["tasks_from"] == "serve-container.yml"
    assert container_task["when"] == MODE


def test_live_guest_plays_run_only_in_container_mode() -> None:
    plays = yaml.safe_load((ROOT / "playbooks/llm-model-trial.yml").read_text())
    for play in (plays[3], plays[5]):
        task = play["tasks"][0]
        assert task["when"] == MODE
        assert play["hosts"] == "llm_gpu_group"


def test_container_files_download_on_the_target_before_the_baseline_stops() -> None:
    plays = yaml.safe_load((ROOT / "playbooks/llm-model-trial.yml").read_text())
    prestage = plays[2]
    task = prestage["tasks"][0]
    assert prestage["hosts"] == "llm_model_trial_candidate_group"
    assert prestage["any_errors_fatal"] is True
    assert task["ansible.builtin.include_role"]["tasks_from"] == "prestage-container.yml"
    assert task["when"] == [
        MODE,
        "hostvars['localhost'].llm_model_trial_container_settings.separate_guest | bool",
    ]
    flow = load("prestage-container.yml")
    assert names(flow) == [
        "Bind the controller's validated plan to the candidate guest",
        "Read free bytes under the candidate model cache",
        "Require free space for the selected files plus 10 percent",
        "Download the selected files before the baseline stops",
    ]
    assert include_of(flow[0]) == "prepare.yml"
    assert include_of(flow[3]) == "download.yml"
    assert flow[1]["register"] == "llm_model_trial_cache_free"
    gate = " ".join(flow[2]["ansible.builtin.assert"]["that"][0].split())
    assert gate == (
        "(llm_model_trial_cache_free.stdout_lines[-1] | int) >= "
        "((llm_model_trial_plan.files | map(attribute='size') | sum) * 1.1) | int"
    )


def test_container_prestage_gate_evaluates_free_space() -> None:
    flow = load("prestage-container.yml")
    expr = " ".join(flow[2]["ansible.builtin.assert"]["that"][0].split())
    env = jinja2.Environment()
    check = env.compile_expression(expr)
    plan = {"files": [{"size": 100}, {"size": 900}]}
    assert check(llm_model_trial_cache_free={"stdout_lines": ["Avail", "1100"]}, llm_model_trial_plan=plan)
    assert not check(llm_model_trial_cache_free={"stdout_lines": ["Avail", "1099"]}, llm_model_trial_plan=plan)


def test_candidate_failure_does_not_fail_the_candidate_play() -> None:
    serve = load("serve-container.yml")
    block = next(task for task in serve if "block" in task)
    assert [include_of(task) for task in block["rescue"]] == ["failure-evidence.yml"]
    assert [include_of(task) for task in block["always"]] == ["restore-candidate.yml"]
    failure = load("failure-evidence.yml")
    fail_task = next(task for task in failure if "ansible.builtin.fail" in task)
    assert fail_task["when"] == "not (llm_model_trial_container_mode | default(false) | bool)"
    assert any(task.get("name", "").startswith("Record the candidate failure") for task in failure)


def test_restore_container_restores_baseline_then_always_removes_timer_then_asserts() -> None:
    restore = load("restore-container.yml")
    block = next(task for task in restore if "block" in task)
    assert [include_of(task) for task in block["block"]] == ["restore-baseline.yml"]
    always = names(block["always"])
    assert "Stop the baseline restore timer" in always
    assert "Remove the baseline restore timer units" in always
    assert always.index("Stop the baseline restore timer") < always.index(
        "Remove the baseline restore timer units"
    )
    assert restore[-2] is block
    assert restore[-1]["ansible.builtin.assert"]["that"] == ["not (llm_model_trial_candidate_failed | bool)"]


def test_container_suspend_probes_locks_arms_then_stops_the_baseline() -> None:
    suspend = load("container-suspend.yml")
    ordered = names(suspend)
    probe_name = "Probe the fallback endpoint before the baseline is touched"
    lock_name = "Bind the plan, take the host lock, and identify the baseline"
    arm_name = "Arm the baseline restore timer before the baseline stops"
    stop_name = "Stop the recorded baseline unit behind the armed timer"
    assert ordered.index(probe_name) < ordered.index(lock_name) < ordered.index(arm_name) < ordered.index(stop_name)
    assert include_of(next(task for task in suspend if task.get("name") == lock_name)) == "lock.yml"
    probe = next(task for task in suspend if task.get("name") == probe_name)
    assert probe["when"] == "llm_model_trial_fallback_probe_url | length > 0"
    assert probe["ansible.builtin.uri"]["status_code"] == 200
    assert probe["ansible.builtin.uri"]["url"].endswith("/v1/models")


def test_default_acquire_flow_keeps_download_stop_and_health_in_order() -> None:
    flow = [task for task in load("acquire-and-serve.yml")]
    includes = [include_of(task) for task in flow if "ansible.builtin.include_tasks" in task]
    assert includes == ["download.yml", "candidate-health.yml", "candidate-facts.yml"]
    assert "Stop the recorded baseline unit" in names(flow)
    assert names(flow).index("Stop the recorded baseline unit") < names(flow).index(
        "Render the run-scoped trial systemd unit"
    )


def test_container_acquire_asserts_vllm_and_renders_the_container_unit() -> None:
    flow = load("acquire-and-serve-container.yml")
    assert flow[0]["ansible.builtin.assert"]["that"] == ["llm_model_trial_plan.engine == 'vllm'"]
    template = next(task for task in flow if "ansible.builtin.template" in task)
    assert template["ansible.builtin.template"]["src"] == "llm-model-trial-container.service.j2"
    assert "Stop the recorded baseline unit" not in names(flow)


def test_timer_template_arms_the_baseline_restore_for_the_window() -> None:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters.update(comment=lambda value: "# " + str(value), quote=shlex.quote)
    values = {
        "ansible_managed": "managed by test",
        "llm_model_trial_id": "trial123",
        "llm_model_trial_window_minutes": 45,
        "llm_model_trial_baseline_unit": "llm-gpu-serving-medium.service",
        "llm_model_trial_restore_name": "llm-model-trial-restore-trial123",
    }
    timer = env.from_string((ROLE / "templates/llm-model-trial-restore.timer.j2").read_text()).render(**values)
    service = env.from_string((ROLE / "templates/llm-model-trial-restore.service.j2").read_text()).render(**values)
    assert "OnActiveSec=45min" in timer
    assert "Unit=llm-model-trial-restore-trial123.service" in timer
    assert "ExecStart=/usr/bin/systemctl start llm-gpu-serving-medium.service" in service
