"""The co-resident validation accepts the main profile the medium GPU host selects."""

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# 95 GiB of device memory in MiB, as nvidia-smi reports it to assert-profile-fit.
DEVICE_MEMORY_MIB = 95 * 1024
SCENARIO_PROFILES = {
    "main": {"engine": "vllm", "gpu_memory_utilization": 0.75},
    "small-c4-64k": {"gpu_memory_reserve_gib": 20},
    "small-k2-horizon": {"gpu_memory_reserve_gib": 20},
}


def _load(path: str):
    return yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _jinja() -> jinja2.Environment:
    env = jinja2.Environment()
    # `extract` is an Ansible filter; map('extract', container) indexes container per item.
    env.filters["extract"] = lambda value, container: container[value]
    return env


def test_co_resident_validation_leaves_slot_count_to_the_profile() -> None:
    host_vars = yaml.safe_load(
        next((REPO_ROOT / "inventory/host_vars").glob("llm-blackwell-*-medium.yml")).read_text(encoding="utf-8")
    )
    assert host_vars["llm_gpu_serving_co_resident_profiles"]
    tasks = _load("roles/llm_gpu_serving/tasks/validate-profiles.yml")
    checks = _task(tasks, "Validate the selected co-resident GPU profiles")["ansible.builtin.assert"]["that"]
    assert not [check for check in checks if "max_num_seqs" in check]


def _combined_reserve_fits(co_resident_profiles: list[str]) -> bool:
    tasks = _load("roles/llm_gpu_serving/tasks/main.yml")
    task = _task(tasks, "Assert the combined co-resident GPU reserve fits outside vLLM")
    expression = task["ansible.builtin.assert"]["that"][0]
    rendered = _jinja().from_string("{{ " + expression + " }}").render(
        llm_gpu_serving_profiles=SCENARIO_PROFILES,
        llm_gpu_serving_co_resident_profiles=co_resident_profiles,
        llm_active_profile="main",
        nvidia_gpu_guest_fit_gpu_memory_mib=DEVICE_MEMORY_MIB,
    )
    return rendered == str(True)


def test_combined_reserve_rejects_two_20_gib_co_residents_on_a_95_gib_device() -> None:
    # 0.75 utilization on 95 GiB leaves 23.75 GiB outside vLLM; two 20 GiB reserves need 40.
    assert not _combined_reserve_fits(["small-c4-64k", "small-k2-horizon"])


def test_combined_reserve_accepts_one_20_gib_co_resident_on_a_95_gib_device() -> None:
    assert _combined_reserve_fits(["small-c4-64k"])
