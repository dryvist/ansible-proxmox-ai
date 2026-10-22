"""Every GPU deployment the router renders names its serving profile in model_info.

The Glance profile tile lists a LiteLLM deployment only when `model_info.profile`
is set, so each profile's entry must carry it into the rendered config.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "roles/llm_router/templates"
VLLM_DEFAULTS = REPO_ROOT / "roles/vllm_serving/defaults/main/10-profiles.yml"
LLAMACPP_DEFAULTS = REPO_ROOT / "roles/llamacpp_serving/defaults/main/10-profiles.yml"
SGLANG_DEFAULTS = REPO_ROOT / "roles/sglang_serving/defaults/main/10-profiles.yml"
LEGACY_DEFAULTS = REPO_ROOT / "roles/llm_gpu_serving/defaults/main/10-profiles.yml"
RETIRED_FLASH_NEXT_NVFP4_PROFILES = {
    "flash-next-64k-1-fp8-mtp",
    "flash-next-64k-4-fp8-mtp",
    "flash-next-64k-1-fp8",
    "flash-next-64k-4-fp8",
    "flash-next-64k-8-fp8",
    "flash-next-64k-8-fp8-mtp0",
    "flash-next-64k-8-fp8-mtp2",
    "flash-next-64k-8-fp8-mtp4",
    "flash-next-262k-1-fp8",
    "flash-next-262k-2-fp8",
}


@lru_cache(maxsize=1)
def _entries() -> list[dict]:
    """Read the router registry after its production catalog projection."""
    with tempfile.TemporaryDirectory(prefix="llm-router-catalog-projection-") as temp_dir:
        output = Path(temp_dir) / "gpu-registry.json"
        result = subprocess.run(
            [
                "ansible-playbook",
                str(REPO_ROOT / "tests/llm_router/fixtures/project_catalog_entries.yml"),
                "-i",
                "localhost,",
                "-c",
                "local",
                "--extra-vars",
                json.dumps({"projection_output": str(output)}),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        projected = json.loads(output.read_text(encoding="utf-8"))
    return [entry for entry in projected if entry["tier"] == "gpu"]


def _serving_profiles() -> dict:
    vllm_core = yaml.safe_load(VLLM_DEFAULTS.read_text(encoding="utf-8"))[
        "vllm_serving_profiles_core"
    ]
    vllm_families = yaml.safe_load(
        VLLM_DEFAULTS.with_name("11-profile-families.yml").read_text(encoding="utf-8")
    )["vllm_serving_profiles_specialized"]
    vllm = {**vllm_core, **vllm_families}
    llamacpp = yaml.safe_load(LLAMACPP_DEFAULTS.read_text(encoding="utf-8"))["llamacpp_serving_profiles"]
    sglang = yaml.safe_load(SGLANG_DEFAULTS.read_text(encoding="utf-8"))["sglang_serving_profiles"]
    legacy = yaml.safe_load(LEGACY_DEFAULTS.read_text(encoding="utf-8"))["llm_profiles"]
    return {**legacy, **vllm, **llamacpp, **sglang}


def _combine(original: dict, update: dict, recursive: bool = False) -> dict:
    merged = dict(original)
    for key, value in update.items():
        if recursive and isinstance(merged.get(key), dict) and isinstance(value, dict):
            value = _combine(merged[key], value, recursive=True)
        merged[key] = value
    return merged


def _render(models: list[dict]) -> list[dict]:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
        trim_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["to_json"] = json.dumps
    env.filters["combine"] = _combine
    env.filters["to_nice_yaml"] = lambda value, indent=2, sort_keys=True: yaml.safe_dump(
        value, default_flow_style=False, indent=indent, sort_keys=sort_keys
    )
    env.tests["search"] = lambda value, pattern: re.search(pattern, value) is not None
    env.globals.update(
        litellm_model=lambda model: model.get(
            "litellm_model_name", f"{model['provider']}/{model['client_model_id']}"
        ),
        local_bounds=lambda model: "",
        delegation_hints=lambda model: "",
    )
    text = env.get_template("model-list-gpu.yaml.j2").render(
        llm_router_gpu_models=models,
        llm_router_deployment_tags_by_entry={f"{m['client_model_id']}|{m['tier']}": [] for m in models},
        llm_router_llm_gpu_base_url="http://placeholder/v1",
        llm_router_llm_gpu_co_resident_base_url="http://placeholder:10435/v1",
        llm_router_gpu_profile_base_urls={
            m["profile"]: "http://placeholder:10436/v1"
            for m in models
            if m.get("api_port_key") == "llm_fast_trial_api"
        },
        llm_router_gpu_co_resident_profiles=[m["profile"] for m in models if m.get("co_resident_only")],
        llm_router_light_api_key="placeholder",
    )
    return yaml.safe_load("model_list:\n" + text)["model_list"]


def test_every_serving_profile_is_declared_by_exactly_one_gpu_registry_entry():
    profiles = [entry["profile"] for entry in _entries()]
    serving = _serving_profiles()
    assert len(profiles) == len(set(profiles))
    assert set(profiles) == set(serving) - RETIRED_FLASH_NEXT_NVFP4_PROFILES
    assert all(not serving[name]["enabled"] for name in RETIRED_FLASH_NEXT_NVFP4_PROFILES)
    assert all(entry["tier"] == "gpu" for entry in _entries())


def test_each_profile_renders_its_name_into_model_info():
    for entry in _entries():
        rendered = _render([entry])
        assert len(rendered) == 1
        assert rendered[0]["litellm_params"]["model"] == (
            f"{entry['provider']}/{entry['client_model_id']}"
        )
        assert rendered[0]["model_info"]["profile"] == entry["profile"]
        assert rendered[0]["model_name"] == entry.get("route_group", entry["client_model_id"])


def test_co_resident_profile_uses_the_second_inventory_derived_port():
    entries = [entry for entry in _entries() if entry.get("co_resident_only")]
    assert entries

    for entry in entries:
        rendered = _render([entry])[0]

        expected_api_base = (
            "http://placeholder:10436/v1"
            if entry.get("api_port_key") == "llm_fast_trial_api"
            else "http://placeholder:10435/v1"
        )
        assert rendered["litellm_params"]["api_base"] == expected_api_base
        assert rendered["litellm_params"]["max_parallel_requests"] == 4


def test_a_deployment_without_a_profile_renders_no_profile_marker():
    entry = {key: value for key, value in _entries()[0].items() if key != "profile"}
    assert "profile" not in _render([entry])[0]["model_info"]
