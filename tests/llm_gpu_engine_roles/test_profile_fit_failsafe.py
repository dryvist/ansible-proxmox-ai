"""Exercise profile-fit admission against registered artifact sizes."""

from pathlib import Path

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
