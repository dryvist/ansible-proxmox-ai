"""Every GPU deployment the router renders names its serving profile in model_info.

The Glance profile tile lists a LiteLLM deployment only when `model_info.profile`
is set, so each profile's entry must carry it into the rendered config.
"""

from __future__ import annotations

import json
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "roles/llm_router/templates"
REGISTRY_FILE = REPO_ROOT / "llm-models.d/60-gpu-pro6000.yml"
VLLM_DEFAULTS = REPO_ROOT / "roles/vllm_serving/defaults/main/10-profiles.yml"
LLAMACPP_DEFAULTS = REPO_ROOT / "roles/llamacpp_serving/defaults/main/10-profiles.yml"


def _entries() -> list[dict]:
    return yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))["_llm_registry_gpu_pro6000"]


def _serving_profiles() -> dict:
    vllm = yaml.safe_load(VLLM_DEFAULTS.read_text(encoding="utf-8"))["vllm_serving_profiles"]
    llamacpp = yaml.safe_load(LLAMACPP_DEFAULTS.read_text(encoding="utf-8"))["llamacpp_serving_profiles"]
    return {**vllm, **llamacpp}


def _render(models: list[dict]) -> list[dict]:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
        trim_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["to_json"] = json.dumps
    env.globals.update(
        litellm_model=lambda model: f"openai/{model['client_model_id']}",
        local_bounds=lambda model: "",
        delegation_hints=lambda model: "",
    )
    text = env.get_template("model-list-gpu.yaml.j2").render(
        llm_router_gpu_models=models,
        llm_router_deployment_tags_by_entry={f"{m['client_model_id']}|{m['tier']}": [] for m in models},
        llm_router_llm_gpu_base_url="http://placeholder/v1",
        llm_router_light_api_key="placeholder",
    )
    return yaml.safe_load("model_list:\n" + text)["model_list"]


def test_every_serving_profile_is_declared_by_exactly_one_gpu_registry_entry():
    profiles = [entry["profile"] for entry in _entries()]
    serving = _serving_profiles()
    assert sorted(profiles) == sorted(serving)
    assert all(entry["tier"] == "gpu" for entry in _entries())


def test_each_profile_renders_its_name_into_model_info():
    for entry in _entries():
        rendered = _render([entry])
        assert len(rendered) == 1
        assert rendered[0]["model_info"]["profile"] == entry["profile"]
        assert rendered[0]["model_name"] == entry["client_model_id"]


def test_a_deployment_without_a_profile_renders_no_profile_marker():
    entry = {key: value for key, value in _entries()[0].items() if key != "profile"}
    assert "profile" not in _render([entry])[0]["model_info"]
