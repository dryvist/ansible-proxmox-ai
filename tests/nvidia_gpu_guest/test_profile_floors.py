"""Check the per-profile serving floor schema against the shipped profiles and registry."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/nvidia_gpu_guest"
PROFILE_DEFAULTS = {
    **yaml.safe_load((REPO_ROOT / "roles/vllm_serving/defaults/main/10-profiles.yml").read_text(encoding="utf-8"))["vllm_serving_profiles"],
    **yaml.safe_load((REPO_ROOT / "roles/llamacpp_serving/defaults/main/10-profiles.yml").read_text(encoding="utf-8"))["llamacpp_serving_profiles"],
}
REGISTRY_FILE = REPO_ROOT / "llm-models.d/60-gpu-pro6000.yml"
FLOOR_FIELDS = {
    "concurrency",
    "input_len",
    "output_len",
    "min_tok_s_per_agent",
    "max_ttft_p90_ms",
    "evidence",
}


def _profiles() -> dict:
    return PROFILE_DEFAULTS


def _entries() -> list[dict]:
    return yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))["_llm_registry_gpu_pro6000"]


def _floor_complete(floor: object) -> bool:
    if not isinstance(floor, dict) or not FLOOR_FIELDS <= floor.keys():
        return False
    numeric = ("concurrency", "input_len", "output_len", "min_tok_s_per_agent", "max_ttft_p90_ms")
    return all(float(floor[key]) > 0 for key in numeric) and bool(str(floor["evidence"]).strip())


def test_a_servable_registry_entry_must_carry_a_complete_floor():
    profiles = _profiles()
    for entry in _entries():
        if entry.get("servable") is True:
            assert _floor_complete(profiles[entry["profile"]].get("floor")), entry["profile"]


def test_a_declared_floor_is_never_partial():
    for name, profile in _profiles().items():
        if "floor" in profile:
            assert _floor_complete(profile["floor"]), name


def test_the_floor_checker_rejects_partial_and_empty_floors():
    full = dict.fromkeys(FLOOR_FIELDS, 1)
    full["evidence"] = "reference"
    assert _floor_complete(full)
    assert not _floor_complete({key: value for key, value in full.items() if key != "max_ttft_p90_ms"})
    assert not _floor_complete({**full, "evidence": " "})
    assert not _floor_complete({**full, "min_tok_s_per_agent": 0})
    assert not _floor_complete(None)


def test_the_role_assertion_names_the_same_floor_fields_as_the_schema():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/assert-profile-floors.yml").read_text(encoding="utf-8"))
    complete = next(task for task in tasks if task["name"].startswith("Assert every declared profile floor"))
    conditions = "\n".join(complete["ansible.builtin.assert"]["that"])
    assert all(field in conditions for field in FLOOR_FIELDS)
    load_tasks = yaml.safe_load((ROLE_ROOT / "tasks/load-registry.yml").read_text(encoding="utf-8"))
    assert any(task.get("ansible.builtin.include_tasks") == "assert-profile-floors.yml" for task in load_tasks)
