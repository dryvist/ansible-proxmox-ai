"""The legacy and engine-specific GPU serving roles share one pause contract."""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PAUSE_VAR = "llm_gpu_serving_paused"
ROLES = (
    {
        "name": "llm_gpu_serving",
        "activation": "roles/llm_gpu_serving/tasks/main.yml",
        "health": "roles/llm_gpu_serving/tasks/health-check.yml",
        "active_profile": "llm_active_profile",
        "state_register": "llm_gpu_serving_profile_state",
    },
    {
        "name": "llamacpp_serving",
        "activation": "roles/llamacpp_serving/tasks/activate.yml",
        "health": "roles/llamacpp_serving/tasks/health-check.yml",
        "active_profile": "llamacpp_serving_active_profile",
        "state_register": "llamacpp_serving_profile_state",
    },
    {
        "name": "vllm_serving",
        "activation": "roles/vllm_serving/tasks/activate.yml",
        "health": "roles/vllm_serving/tasks/health-check.yml",
        "active_profile": "vllm_serving_active_profile",
        "state_register": "vllm_serving_profile_state",
    },
)


def _load(path: str):
    return yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _jinja() -> jinja2.Environment:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["bool"] = bool
    return env


def test_shared_pause_flag_defaults_to_false():
    assert _load("inventory/group_vars/all.yml")[PAUSE_VAR] is False


def test_pause_stops_the_selected_unit_without_disabling_it():
    env = _jinja()
    for role in ROLES:
        handler = _task(
            _load(f"roles/{role['name']}/handlers/main.yml"),
            "Start the active GPU serving profile",
        )["ansible.builtin.systemd"]
        assert handler["enabled"] is True, role["name"]
        for paused, expected in ((True, "stopped"), (False, "restarted")):
            state = env.from_string(handler["state"]).render(**{PAUSE_VAR: paused})
            assert state == expected, (role["name"], paused, state)


def test_pause_and_resume_reconcile_service_state_and_keep_the_health_gate():
    env = _jinja()
    for role in ROLES:
        tasks = _load(role["activation"])
        state_check = _task(tasks, "Check profile service states and request a switch when they diverge")
        changed_when = state_check["changed_when"]
        for paused, rc, expected in (
            (True, 0, True),
            (True, 3, False),
            (False, 0, False),
            (False, 3, True),
        ):
            changed = env.from_string("{{ " + changed_when + " }}").render(
                item={"key": "active"},
                **{
                    role["active_profile"]: "active",
                    role["state_register"]: {"rc": rc},
                    PAUSE_VAR: paused,
                },
            )
            assert changed == str(expected), (role["name"], paused, rc, changed)

        health_include = _task(tasks, "Wait for the active GPU serving profile to answer")
        assert health_include["when"] == f"not ({PAUSE_VAR} | default(false) | bool)", role["name"]
        health_tasks = _load(role["health"])
        probe = _task(health_tasks[0]["block"], "Probe the active GPU serving profile until it answers")
        assert probe["ansible.builtin.uri"]["status_code"] == 200, role["name"]
        assert probe["until"].endswith(".status == 200"), role["name"]


def test_stress_window_sequence_is_documented():
    readme = (REPO_ROOT / "roles/llm_gpu_serving/README.md").read_text(encoding="utf-8")
    sequence = re.sub(r"\s+", " ", readme)
    assert (
        "template 76` with `llm_gpu_serving_paused=true`, then `template 18` with "
        "`smoke` or `full`, then `template 76` with `llm_gpu_serving_paused=false`"
    ) in sequence
    for name in ("llamacpp_serving", "vllm_serving"):
        engine_readme = (REPO_ROOT / f"roles/{name}/README.md").read_text(encoding="utf-8")
        assert "llm_gpu_serving_paused" in engine_readme
