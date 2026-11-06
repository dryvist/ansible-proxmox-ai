"""Hub-response fixtures exercise proposal gates without network or serving."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "model_watch", ROOT / "roles/llm_model_watch/filter_plugins/model_watch.py"
)
PLUGIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGIN)
MODELS = json.loads((Path(__file__).parent / "fixtures/watch-models.json").read_text())
GPU = {"id": "gpu", "vram_gib": 8, "reserve_gib": 2, "formats": ["gguf"],
       "libraries": ["gguf"], "quantizations": ["Q4_*"]}


def select(models=None, accelerators=None):
    return PLUGIN.select_model_watch(
        MODELS if models is None else models, accelerators or [GPU], ["unsloth", "mlx-community"]
    )


def test_fits_complete_quantized_weights_with_runtime_reserve():
    request, = select()
    assert request["weight_bytes"] == 4 * 1024**3
    assert request["quantization"] == "Q4_K_M"
    assert request["revision"] == "a" * 40
    assert len(request["key"]) == 64


def test_each_accelerator_has_independent_format_quant_and_vram_gates():
    apple = {**GPU, "id": "apple", "formats": ["safetensors"], "libraries": ["mlx"],
             "quantizations": ["mlx-4bit"]}
    assert {item["accelerator"] for item in select(accelerators=[GPU, apple])} == {"gpu", "apple"}
    assert select(accelerators=[{**GPU, "vram_gib": 5}]) == []
    assert select(accelerators=[{**GPU, "quantizations": ["Q8_*"]}]) == []


@pytest.mark.parametrize("field,value", [
    ("private", True), ("gated", "auto"), ("private", None),
    ("sha", "main"), ("id", "stranger/example"), ("library_name", "unknown"),
])
def test_untrusted_or_unpinned_models_are_rejected(field, value):
    data = copy.deepcopy(MODELS[:1])
    data[0][field] = value
    assert select(data) == []


@pytest.mark.parametrize("change", ["missing_size", "incomplete", "duplicate"])
def test_unknown_sizes_and_incomplete_shards_are_rejected(change):
    data = copy.deepcopy(MODELS[:1])
    if change == "missing_size":
        del data[0]["siblings"][0]["size"]
    elif change == "incomplete":
        del data[0]["siblings"][0]
    else:
        data[0]["siblings"][1] = data[0]["siblings"][0]
    assert select(data) == []


def test_requests_are_stable_deduplicated_and_revision_scoped():
    assert select(MODELS + MODELS) == select()
    data = copy.deepcopy(MODELS)
    data[0]["sha"] = "c" * 40
    assert select(data)[0]["key"] != select()[0]["key"]


def test_safetensors_require_known_quantization_and_complete_shards():
    accelerator = {**GPU, "formats": ["safetensors"], "libraries": ["transformers"],
                   "quantizations": ["awq"]}
    data = copy.deepcopy(MODELS[1:])
    data[0]["library_name"] = "transformers"
    data[0]["config"] = {"quantization_config": {"quant_method": "awq"}}
    assert len(select(data, [accelerator])) == 1
    data[0]["siblings"][0]["rfilename"] = "model-00001-of-00002.safetensors"
    assert select(data, [accelerator]) == []
    data[0]["siblings"][0]["rfilename"] = "model.safetensors"
    data[0]["config"] = {}
    assert select(data, [accelerator]) == []


def test_mlx_native_quantization_metadata():
    accelerator = {**GPU, "formats": ["safetensors"], "libraries": ["mlx"],
                   "quantizations": ["mlx-4bit"]}
    data = copy.deepcopy(MODELS[1:])
    data[0]["config"] = {"quantization": {"bits": 4}}
    assert len(select(data, [accelerator])) == 1
