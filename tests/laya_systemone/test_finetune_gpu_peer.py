"""Contracts for freeing a shared GPU peer during Laya fine-tuning."""

from __future__ import annotations

import jinja2
import yaml
from test_finetune_contract import DEFAULTS, REPO_ROOT, ROLE, _find_task, _load_tasks, _walk_tasks

CLASSIFY_HOST_VARS_PATH = REPO_ROOT / "inventory/host_vars/llm-ada-lovelace-6X-16GB-classify.yml"
PEER_HOST_VARS_PATH = REPO_ROOT / "inventory/host_vars/llm-ada-lovelace-6X-16GB-embed.yml"
NVIDIA_DEFAULTS_PATH = REPO_ROOT / "roles/nvidia_gpu_guest/defaults/main/00-core.yml"


def test_gpu_peer_stops_only_when_active_and_train_has_a_bound_and_device_selection() -> None:
    tasks = _load_tasks()
    training_name = "Prepare and dispatch the fine-tuned checkpoint when its manifest is stale"
    training = _find_task(tasks, training_name)
    ordered = list(_walk_tasks([training]))
    state = _find_task([training], "Read the GPU peer serving unit state before training")
    record_state = _find_task([training], "Record whether the GPU peer serving unit was active")
    stop = _find_task([training], "Stop the active GPU peer serving unit for training")
    persist_state = _find_task([training], "Persist the GPU peer state for the collect run")
    train = _find_task([training], "Launch the Laya training command as a detached systemd job")

    assert state["delegate_to"] == "{{ laya_systemone_finetune_gpu_peer_host }}"
    assert "laya_systemone_finetune_device == 'cuda'" in state["when"]
    assert "laya_systemone_finetune_gpu_peer_host | length > 0" in state["when"]
    assert record_state["ansible.builtin.set_fact"]["laya_systemone_finetune_gpu_peer_was_active"] == (
        "{{ laya_systemone_finetune_gpu_peer_state.rc == 0 }}"
    )
    assert stop["delegate_to"] == "{{ laya_systemone_finetune_gpu_peer_host }}"
    assert stop["when"] == "laya_systemone_finetune_gpu_peer_was_active | default(false) | bool"
    assert ordered.index(state) < ordered.index(persist_state) < ordered.index(stop) < ordered.index(train)
    assert persist_state["ansible.builtin.copy"]["dest"] == "{{ laya_systemone_finetune_dispatch_state_path }}"
    assert "gpu_peer_was_active" in persist_state["ansible.builtin.copy"]["content"]
    assert train["ansible.builtin.command"]["argv"][0] == "systemd-run"
    assert "--no-block" in train["ansible.builtin.command"]["argv"]
    assert train["no_log"] is True
    argv = train["ansible.builtin.command"]["argv"]
    device_index = argv.index("--device")
    assert argv[device_index + 1] == "{{ laya_systemone_finetune_device }}"
    assert not any("GPU peer" in task.get("name", "") for task in tasks)


def test_peer_unit_and_health_url_are_derived_from_inventory_and_tofu_constants() -> None:
    classify = yaml.safe_load(CLASSIFY_HOST_VARS_PATH.read_text())
    peer = yaml.safe_load(PEER_HOST_VARS_PATH.read_text())
    nvidia_defaults = yaml.safe_load(NVIDIA_DEFAULTS_PATH.read_text())
    peer_host = classify["laya_systemone_finetune_gpu_peer_host"]
    assert peer_host == "llm-ada-lovelace-6X-16GB-embed"

    environment = jinja2.Environment()
    context = {
        "laya_systemone_finetune_gpu_peer_host": peer_host,
        "nvidia_gpu_guest_unit_prefix": nvidia_defaults["nvidia_gpu_guest_unit_prefix"],
        "hostvars": {peer_host: {**peer, "ansible_facts": {"default_ipv4": {"address": "192.0.2.10"}}}},
        "tofu_data": {"constants": {"service_ports": {peer["llm_gpu_serving_api_port_key"]: 12345}}},
    }
    unit = environment.from_string(classify["laya_systemone_finetune_gpu_peer_unit"]).render(**context)
    health_url = environment.from_string(
        classify["laya_systemone_finetune_gpu_peer_health_url"]
    ).render(**context)

    assert unit == (
        f"{nvidia_defaults['nvidia_gpu_guest_unit_prefix']}-{peer['llm_active_profile']}.service"
    )
    # The serving unit binds the listen host, else the peer's default IPv4 (llamacpp-serving.service.j2:23).
    assert health_url == "http://192.0.2.10:12345/v1/models"
    assert "nvidia_gpu_guest_listen_host" in classify["laya_systemone_finetune_gpu_peer_health_url"]
    assert "llm_gpu_serving_api_port_key" in classify["laya_systemone_finetune_gpu_peer_health_url"]
    assert "tofu_data.constants.service_ports" in classify["laya_systemone_finetune_gpu_peer_health_url"]


def test_gpu_peer_defaults_are_inert_until_a_host_names_a_peer() -> None:
    assert DEFAULTS["laya_systemone_finetune_device"] == "cuda"
    assert DEFAULTS["laya_systemone_finetune_gpu_peer_host"] == ""
    assert DEFAULTS["laya_systemone_finetune_gpu_peer_unit"] == ""
    assert DEFAULTS["laya_systemone_finetune_gpu_peer_health_url"] == ""
    assert DEFAULTS["laya_systemone_finetune_train_timeout_seconds"] == 2700


def test_restore_tasks_wait_for_laya_and_the_gpu_peer_with_a_bounded_window() -> None:
    restore_tasks = _load_tasks(ROLE / "tasks/finetune-restore-services.yml")

    laya_health = _find_task(restore_tasks, "Wait for Laya health after GPU fine-tuning")
    assert laya_health["ansible.builtin.uri"]["url"] == (
        "http://127.0.0.1:{{ laya_systemone_port }}/health"
    )
    assert laya_health["retries"] == "{{ laya_systemone_finetune_health_retries }}"
    assert laya_health["delay"] == "{{ laya_systemone_finetune_health_delay_seconds }}"
    assert laya_health["ansible.builtin.uri"]["timeout"] == "{{ laya_systemone_finetune_health_timeout_seconds }}"
    assert laya_health["ignore_errors"] is True

    peer_start = _find_task(restore_tasks, "Start the GPU peer serving unit after fine-tuning")
    peer_health = _find_task(restore_tasks, "Wait for GPU peer serving health after fine-tuning")
    assert peer_start["ansible.builtin.systemd"]["state"] == "started"
    assert peer_start["delegate_to"] == "{{ laya_systemone_finetune_gpu_peer_host }}"
    assert "laya_systemone_finetune_gpu_peer_was_active" in peer_start["when"]
    assert peer_start["ignore_errors"] is True
    assert peer_health["ansible.builtin.uri"]["url"] == "{{ laya_systemone_finetune_gpu_peer_health_url }}"
    assert peer_health["delegate_to"] == "{{ laya_systemone_finetune_gpu_peer_host }}"
    assert peer_health["retries"] == "{{ laya_systemone_finetune_health_retries }}"
    assert peer_health["delay"] == "{{ laya_systemone_finetune_health_delay_seconds }}"
    assert peer_health["ansible.builtin.uri"]["timeout"] == "{{ laya_systemone_finetune_health_timeout_seconds }}"
    assert "laya_systemone_finetune_gpu_peer_was_active" in peer_health["when"]
    assert peer_health["ignore_errors"] is True
