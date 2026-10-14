"""Task-level contract for fabric_watchdog's registry-derived LLM probes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_TASKS = REPO_ROOT / "roles/fabric_watchdog/tasks/main.yml"
PROBE_TASKS = REPO_ROOT / "roles/fabric_watchdog/tasks/probe-targets.yml"
ROUTER_DEFAULTS = REPO_ROOT / "roles/llm_router/defaults/main"

LARGE_URL = "https://large.example.invalid/v1/models"
LIGHT_URL = "https://light.example.invalid/v1/models"
CPU_URL = "https://cpu.example.invalid/v1/models"
VLLM_URL = "https://vllm.example.invalid/v1/models"


def run_task_contract(
    tmp_path: Path,
    *,
    cpu_urls: list[str] | None = None,
    vllm_enabled: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the real projection defaults and watchdog task file with fixtures."""
    cpu_urls = ["https://cpu.example.invalid/v1"] if cpu_urls is None else cpu_urls
    registry = [
        {"client_model_id": "fixture-large", "tier": "local-large", "enabled": True,
         "servable": True, "route_group": "large", "provider": "ollama",
         "upstream_model_id": "fixture/large", "order": 1},
        {"client_model_id": "fixture-light", "tier": "light", "enabled": True,
         "servable": True, "route_group": "embed", "provider": "llama_cpp",
         "upstream_model_id": "fixture/light", "order": 2},
        {"client_model_id": "fixture-light-background", "tier": "light", "enabled": True,
         "servable": False, "route_group": "embed", "provider": "llama_cpp",
         "upstream_model_id": "fixture/light-background", "order": 3},
        {"client_model_id": "fixture-cpu", "tier": "cpu-9b", "enabled": True,
         "servable": True, "route_group": "small", "provider": "llama_cpp",
         "upstream_model_id": "fixture/cpu", "order": 4},
        {"client_model_id": "fixture-vllm", "tier": "vllm", "enabled": True,
         "servable": True, "route_group": "small", "provider": "llama_cpp",
         "upstream_model_id": "fixture/vllm", "order": 5},
    ]
    expected_urls = [LARGE_URL, LIGHT_URL]
    if cpu_urls and cpu_urls[0]:
        expected_urls.append(CPU_URL)
    if vllm_enabled:
        expected_urls.append(VLLM_URL)
    variables = {
        "llm_router_model_registry": registry,
        "llm_router_cpu_pool_backend_urls_by_tier": {"cpu-9b": cpu_urls},
        "llm_router_llm_vllm_enabled": vllm_enabled,
        "llm_router_vllm_precheck_passed": True,
        "llm_router_llm_large_base_url": "https://large.example.invalid/v1",
        "llm_router_llm_light_base_url": "https://light.example.invalid/v1",
        "llm_router_llm_vllm_base_url": "https://vllm.example.invalid/v1",
        "llm_router_light_api_key": "fixture-key",
        "llm_router_deployment_tags_by_entry": {
            f"{entry['client_model_id']}|{entry['tier']}": [] for entry in registry
        },
        "llm_router_local_num_retries": 0,
        "llm_router_local_stream_timeout_seconds": 20,
        "llm_router_local_request_timeout_seconds": 20,
        "expected_llm_urls": expected_urls,
    }
    playbook = tmp_path / "fabric-watchdog-probes.yml"
    playbook.write_text(
        yaml.safe_dump(
            [{
                "name": "Run fabric watchdog probe target contract",
                "hosts": "localhost",
                "gather_facts": False,
                "tasks": [
                    {"name": "Load the actual router role defaults",
                     "ansible.builtin.include_vars": {"dir": str(ROUTER_DEFAULTS)}},
                    {"name": "Set the initial probe target list",
                     "ansible.builtin.set_fact": {"fabric_watchdog_targets": []}},
                    {"name": "Run the real watchdog probe target tasks",
                     "ansible.builtin.include_tasks": str(PROBE_TASKS)},
                    {"name": "Assert the projected target URLs",
                     "ansible.builtin.assert": {"that": [
                         "fabric_watchdog_targets | map(attribute='url') | list == expected_llm_urls"
                     ], "fail_msg": "actual={{ fabric_watchdog_targets | map(attribute='url') | list }} expected={{ expected_llm_urls }}"}},
                ],
            }],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        yaml.safe_dump({"all": {"hosts": {"localhost": {"ansible_connection": "local"}}}}),
        encoding="utf-8",
    )
    assert shutil.which("ansible-playbook"), "run this test inside the repository dev shell"
    ansible_tmp = tmp_path / "ansible-tmp"
    ansible_tmp.mkdir()
    return subprocess.run(
        [
            "ansible-playbook", "-i", str(inventory),
            "-e", json.dumps(variables), str(playbook),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(ansible_tmp), "ANSIBLE_NOCOLOR": "1"},
    )


def test_main_includes_the_shared_projection_task_file() -> None:
    tasks = yaml.safe_load(MAIN_TASKS.read_text(encoding="utf-8"))
    assert any(
        task.get("ansible.builtin.include_tasks") == "probe-targets.yml"
        for task in tasks
    )


def test_projection_defaults_are_loaded_for_the_hermes_play() -> None:
    play = yaml.safe_load((REPO_ROOT / "playbooks/agents.yml").read_text(encoding="utf-8"))[0]
    assert play["hosts"] == "hermes_agent_group"
    assert any(
        task.get("ansible.builtin.import_tasks") == "tasks/load_fabric_registry.yml"
        for task in play["pre_tasks"]
    )
    loader = yaml.safe_load((REPO_ROOT / "playbooks/tasks/load_fabric_registry.yml").read_text(encoding="utf-8"))[0]
    assert loader["ansible.builtin.include_role"] == {
        "name": "llm_router", "tasks_from": "registry.yml", "public": True
    }
    assert (ROUTER_DEFAULTS / "55-role-projections.yml").is_file()


def test_real_assertion_passes_with_all_three_new_tiers_active(tmp_path: Path) -> None:
    result = run_task_contract(tmp_path)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "TASK [Assert every active servable tier has a projected API base]" in output
    assert "TASK [Assert the projected target URLs]" in output
