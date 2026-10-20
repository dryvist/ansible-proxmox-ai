"""Exercise profile-fit admission against registered artifact sizes."""

from pathlib import Path
import json

import jinja2
import yaml

from test_engine_profiles import _artifacts, _profiles, _registry_profiles
from test_sglang_profiles import _sglang_profiles

ROOT = Path(__file__).resolve().parents[2]
FIT_TASKS = yaml.safe_load(
    (ROOT / "roles/nvidia_gpu_guest/tasks/assert-profile-fit.yml").read_text(encoding="utf-8")
)
GIB = 2**30


def _profiles_by_name() -> dict:
    legacy = yaml.safe_load(
        (ROOT / "roles/llm_gpu_serving/defaults/main/10-profiles.yml").read_text(encoding="utf-8")
    )["llm_profiles"]
    return {**legacy, **_profiles(), **_sglang_profiles()}


def _evaluate_facts(
    name: str,
    gpu_memory_mib: int = 98304,
    guest_memory_mb: int = 49152,
) -> tuple[dict, list[bool]]:
    profile = _profiles_by_name()[name]
    artifact = _registry_profiles()[name]["artifact"]
    facts = {
        "nvidia_gpu_guest_active_profile": name,
        "nvidia_gpu_guest_profiles": {name: profile},
        "nvidia_gpu_guest_registry_by_profile": {name: {"artifact": artifact}},
        "nvidia_gpu_guest_fit_gpu_query": {"rc": 0, "stdout_lines": [str(gpu_memory_mib)]},
        "hostvars": {
            "localhost": {
                "tofu_data": {"containers": {"test-guest": {"memory_mb": guest_memory_mb}}}
            }
        },
        "inventory_hostname": "test-guest",
    }
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.filters["bool"] = lambda value: value is True or str(value).lower() in {"yes", "true", "on", "1"}

    for task in FIT_TASKS[1:4]:
        for key, template in task["ansible.builtin.set_fact"].items():
            expression = template.strip()[2:-2].strip()
            facts[key] = environment.compile_expression(expression)(**facts)

    assertion = FIT_TASKS[4]["ansible.builtin.assert"]["that"]
    results = [environment.compile_expression(item)(**facts) for item in assertion]
    return facts, results


def test_27b_and_flash_next_ud_q2_llama_cpp_profiles_fit_declared_budgets():
    for name in ("medium-b", "flash-next-small-128k-8"):
        assert _profiles_by_name()[name]["enabled"] is True
        facts, results = _evaluate_facts(name)
        assert all(results), (name, facts, results)
        assert facts["nvidia_gpu_guest_fit_weights_bytes"] < 96 * GIB
        assert facts["nvidia_gpu_guest_fit_guest_memory_bytes"] == 48 * GIB


def test_qwen38_131k_profile_fits_the_4080_guest_weight_admission():
    facts, results = _evaluate_facts("qwen38-27b-131k-1", gpu_memory_mib=16376, guest_memory_mb=13312)
    assert all(results), (facts, results)
    assert facts["nvidia_gpu_guest_fit_weights_bytes"] == 10934860704
    assert facts["nvidia_gpu_guest_fit_gpu_weights_bytes"] < 16 * GIB
    assert facts["nvidia_gpu_guest_fit_guest_memory_bytes"] == 13 * 1024**3
    assert facts["nvidia_gpu_guest_fit_cpu_weights_bytes"] == 0


def test_moe1_profile_weight_splits_fit_the_4080_guest_weight_admission():
    expected = {
        "moe1-qwen36-n14-s1": (15_322_979_296, 6_811_549_696),
        "moe1-qwen36-n16-s2": (14_349_900_768, 7_784_628_224),
        "moe1-bolt-n38-s1": (15_776_808_960, 6_241_517_568),
        "moe1-bolt-n40-s2": (15_378_874_368, 6_639_452_160),
    }
    for name, (gpu_bytes, cpu_bytes) in expected.items():
        facts, results = _evaluate_facts(name, gpu_memory_mib=16376, guest_memory_mb=13312)
        assert all(results), (name, facts, results)
        assert facts["nvidia_gpu_guest_fit_gpu_weights_bytes"] == gpu_bytes
        assert facts["nvidia_gpu_guest_fit_cpu_weights_bytes"] == cpu_bytes
        assert facts["nvidia_gpu_guest_fit_gpu_weights_bytes"] < 16 * GIB
        assert facts["nvidia_gpu_guest_fit_cpu_weights_bytes"] < facts["nvidia_gpu_guest_fit_guest_budget_bytes"]
        assert facts["nvidia_gpu_guest_fit_guest_budget_bytes"] == 13312 * 1048576 - 1610612736


def test_moe1_shared_units_render_the_registered_cpu_offload_flags():
    profiles = _profiles_by_name()
    expected = {
        "moe1-qwen36-n14-s1": 14,
        "moe1-qwen36-n16-s2": 16,
        "moe1-bolt-n38-s1": 38,
        "moe1-bolt-n40-s2": 40,
    }
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROOT / "roles/llm_gpu_serving/templates"),
        trim_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    environment.filters["comment"] = lambda value: "# " + str(value)
    environment.filters["mandatory"] = lambda value, message="": value
    environment.filters["to_json"] = json.dumps

    for name, layers in expected.items():
        profile = profiles[name]
        registry = _registry_profiles()[name]
        unit = environment.get_template("llm-gpu-serving.service.j2").render(
            ansible_managed="Managed by Ansible",
            ansible_facts={"default_ipv4": {"address": "LISTEN_ADDRESS"}},
            llm_gpu_serving_user="llm-gpu-serving",
            llm_gpu_serving_group="llm-gpu-serving",
            llm_gpu_serving_data_dir="/var/lib/llm-gpu-serving",
            llm_gpu_serving_hf_home="HF_CACHE_HOME",
            llm_gpu_serving_profile_name=name,
            llm_gpu_serving_profile={**profile, "port": 10434},
            llm_gpu_serving_profile_registry_model=registry,
            llm_gpu_serving_profile_model_dir=f"/cache/models/{registry['artifact']['hf_repo']}",
            llm_gpu_serving_llamacpp_install_dir="/opt/llm-gpu-serving/llama.cpp",
            llm_gpu_serving_llamacpp_server_bin="/opt/llm-gpu-serving/llama.cpp/llama-server",
        )
        command = unit.split("ExecStart=", maxsplit=1)[1].split("\n\n[Install]", maxsplit=1)[0]
        command = " ".join(command.replace("\\\n", " ").split())
        assert profile["max_model_len"] == 66560
        assert f"--n-cpu-moe {layers}" in command
        assert "--cache-ram 1024" in command
        assert "--cache-type-k q8_0 --cache-type-v q8_0" in command
        assert "--mmproj" not in command


def test_flash_next_nvfp4_mtp_is_rejected_by_the_guest_ram_budget():
    facts, results = _evaluate_facts("flash-next-64k-8-fp8-mtp")
    artifact = facts["nvidia_gpu_guest_fit_artifact"]
    profile_registry_entry = _registry_profiles()["flash-next-64k-8-fp8-mtp"]
    assert artifact["enabled"] is False
    assert profile_registry_entry["enabled"] is False
    assert facts["nvidia_gpu_guest_fit_weights_bytes"] == 132734506847
    assert facts["nvidia_gpu_guest_fit_cpu_weights_bytes"] > facts["nvidia_gpu_guest_fit_guest_budget_bytes"]
    assert not all(results)


def test_fit_assertion_runs_before_cache_sync_and_profile_switch():
    roles = (
        "vllm_serving",
        "llamacpp_serving",
        "sglang_serving",
    )
    for role in roles:
        tasks = yaml.safe_load((ROOT / f"roles/{role}/tasks/activate.yml").read_text(encoding="utf-8"))
        names = [task.get("name", "") for task in tasks]
        assert names.index("Assert the active artifact fits the GPU and guest RAM budgets") < names.index(
            "Cache the active profile's registered model artifact"
        )
        assert names.index("Assert the active artifact fits the GPU and guest RAM budgets") < names.index(
            "Check profile service states and request a switch when they diverge"
        )
