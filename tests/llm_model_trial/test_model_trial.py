from __future__ import annotations

import importlib.util
import json
import shlex
from pathlib import Path

import jinja2
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/llm_model_trial"
FIXTURES = Path(__file__).parent / "fixtures"
RULES = yaml.safe_load((ROLE / "defaults/main/engine-rules.yml").read_text())
SPEC = importlib.util.spec_from_file_location(
    "model_trial", ROLE / "filter_plugins/model_trial.py"
)
assert SPEC and SPEC.loader
PLUGIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGIN)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def select(data: dict, **kwargs) -> dict:
    return PLUGIN.select_model_trial(
        data["info"],
        data["tree"],
        data["config"],
        data["generation_config"],
        data["hf_quant"],
        data["weight_index"],
        data["card"],
        RULES,
        **kwargs,
    )


def test_engine_rule_table_is_fact_keyed_and_contains_no_model_ids() -> None:
    rules = RULES["llm_model_trial_engine_rules"]
    assert {(item["format"], item["quantization"], item["architecture"], item["mtp_head"], item["engine"])
            for item in rules} == {
                ("safetensors", "modelopt_nvfp4", "qwen3_5", True, "vllm"),
                ("safetensors", "modelopt_nvfp4", "qwen3_5", False, "vllm"),
                ("gguf", "*", "qwen4exp", True, "llama_cpp"),
                ("gguf", "*", "qwen4exp", False, "llama_cpp"),
            }
    assert not any("/" in value for item in rules for value in item.values() if isinstance(value, str))
    assert rules[0]["flags"]["speculative_config"] == {
        "method": "mtp", "num_speculative_tokens": 3
    }


def test_nvidia_modelopt_fixture_selects_vllm_with_mtp() -> None:
    selected = select(fixture("nvidia-qwen38.json"))
    assert selected["rule_version"] == 1
    assert selected["engine"] == "vllm"
    assert selected["quantization"] == "modelopt_nvfp4"
    assert selected["architecture"] == "qwen3_5"
    assert selected["mtp_head"] is True
    assert selected["resolved_sha"] == "482ca0f3832238542f8f5295dde86b5f22711d80"
    assert selected["release_date"] == "2026-09-17T17:16:07+00:00"
    assert [item["path"] for item in selected["weight_files"]] == [
        "model-00001-of-00003.safetensors",
        "model-00002-of-00003.safetensors",
        "model-00003-of-00003.safetensors",
    ]
    assert selected["flags"]["speculative_config"]["method"] == "mtp"


def test_unsloth_gguf_fixture_selects_llama_cpp_and_matching_mtp() -> None:
    selected = select(fixture("unsloth-qwen38-gguf.json"))
    assert selected["engine"] == "llama_cpp"
    assert selected["format"] == "gguf"
    assert selected["quantization"] == "UD-IQ3_XXS"
    assert selected["architecture"] == "qwen4exp"
    assert selected["mtp_head"] is True
    assert selected["release_date"] == "2026-10-06T05:31:22+00:00"
    assert len(selected["weight_files"]) == 3
    assert all("UD-IQ3_XXS" in item["path"] and "/MTP/" not in item["path"]
               for item in selected["weight_files"])


def test_incomplete_gguf_shards_are_rejected() -> None:
    data = fixture("unsloth-qwen38-gguf.json")
    data["tree"] = [item for item in data["tree"]
                    if item["path"] != "UD-IQ3_XXS/model-00003-of-00003.gguf"]
    with pytest.raises(ValueError, match="no complete GGUF quantization"):
        select(data)


@pytest.mark.parametrize(("likes", "downloads"), [(100, 9999), (99, 10000), (99, 9999)])
def test_untrusted_repo_requires_both_popularity_thresholds(likes: int, downloads: int) -> None:
    data = fixture("nvidia-qwen38.json")
    data["info"].update(author="unlisted", likes=likes, downloads=downloads)
    with pytest.raises(ValueError, match="allowlist or popularity threshold"):
        select(data)


def test_unlisted_author_at_both_popularity_thresholds_is_admitted() -> None:
    data = fixture("nvidia-qwen38.json")
    data["info"].update(author="community", likes=100, downloads=10000)
    assert select(data)["engine"] == "vllm"


def test_context_above_card_and_config_maximum_is_rejected() -> None:
    with pytest.raises(ValueError, match="exceeds the validated maximum"):
        select(fixture("nvidia-qwen38.json"), requested_context=262145)


def test_trial_unit_template_renders_vllm_mtp_and_llama_cpp() -> None:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters.update(
        comment=lambda value: "# " + str(value),
        quote=shlex.quote,
        to_json=json.dumps,
        bool=bool,
    )
    template = env.from_string((ROLE / "templates/llm-model-trial@.service.j2").read_text())
    shared = {
        "ansible_managed": "managed by test",
        "llm_model_trial_id": "trial123",
        "llm_gpu_serving_user": "serving",
        "llm_gpu_serving_group": "serving",
        "llm_gpu_serving_hf_home": "/var/lib/serving/.cache/huggingface",
        "llm_model_trial_model_dir": "/models/example/model",
        "llm_gpu_serving_cuda_home": "/usr/local/cuda",
        "llm_gpu_serving_venv": "/opt/serving/venv",
        "llm_gpu_serving_llamacpp_install_dir": "/opt/serving/llama.cpp",
        "llm_gpu_serving_llamacpp_server_bin": "/opt/serving/llama.cpp/llama-server",
        "llm_model_trial_bind_host": "192.0.2.20",
        "llm_model_trial_port": 10435,
        "llm_model_trial_plan": {
            "engine": "vllm",
            "context": 65536,
            "slots": 1,
            "flags": {
                "gpu_memory_utilization": "0.85",
                "kv_cache_dtype": "fp8",
                "linear_backend": "b12x",
                "moe_backend": "auto",
                "prefix_caching": True,
                "quantization": "modelopt",
                "reasoning_parser": "qwen3",
                "speculative_config": {"method": "mtp", "num_speculative_tokens": 3},
            },
            "weight_files": [{"path": "model-00001-of-00003.safetensors"}],
        },
        "hf_repo": "example/model",
    }
    vllm = template.render(**shared)
    assert "Run-scoped GPU model trial trial123" in vllm
    assert "--port 10435" in vllm
    assert "--quantization modelopt" in vllm
    assert '--speculative-config \'{"method": "mtp", "num_speculative_tokens": 3}\'' in vllm
    assert "--reasoning-parser qwen3" in vllm

    shared["llm_model_trial_plan"] = {
        "engine": "llama_cpp",
        "context": 32768,
        "slots": 2,
        "flags": {"gpu_layers": 99, "flash_attention": True, "metrics": True, "jinja": True},
        "weight_files": [{"path": "UD-IQ3_XXS/part-00001.gguf"}],
    }
    llama = template.render(**shared)
    assert "llama-server" in llama
    assert "UD-IQ3_XXS/part-00001.gguf" in llama
    assert "--ctx-size 65536" in llama
    assert "--parallel 2" in llama
    assert "--metrics" in llama
    assert "--jinja" in llama


def test_trial_always_path_restores_and_verifies_baseline() -> None:
    playbook = yaml.safe_load((ROOT / "playbooks/llm-model-trial.yml").read_text())
    assert playbook[0]["ansible.builtin.import_playbook"] == "../inventory/load_tofu.yml"
    tasks = yaml.safe_load((ROLE / "tasks/serve.yml").read_text())
    run = next(task for task in tasks if "block" in task and "always" in task)
    release_include = next(
        task["ansible.builtin.include_tasks"]
        for task in run["always"]
        if "ansible.builtin.include_tasks" in task
    )
    release = yaml.safe_load((ROLE / "tasks" / release_include).read_text())
    restore_include = release[0]["ansible.builtin.include_tasks"]
    always = yaml.safe_load((ROLE / "tasks" / restore_include).read_text())
    always_names = [task["name"] for task in always]
    assert "Always restore the exact recorded baseline serving unit" in always_names
    assert "Verify restored baseline /v1/models after the trial" in always_names
    restore = next(task for task in always if "Always restore" in task["name"])
    stop = next(task for task in always if "Stop the run-scoped candidate" in task["name"])
    candidate_check = next(task for task in always if "Verify the candidate unit" in task["name"])
    verify = next(task for task in always if "Verify restored baseline" in task["name"])
    release = next(task for task in always if "Release this run's host lock" in task["name"])
    restore_assert = next(task for task in always if "Require the recorded baseline" in task["name"])
    assert restore["ansible.builtin.systemd"]["state"] == "started"
    assert "/v1/models" in verify["ansible.builtin.uri"]["url"]
    assert verify["ansible.builtin.uri"]["status_code"] == 200
    assert restore_assert["ansible.builtin.assert"]["that"] == [
        "llm_model_trial_restore_proof.verified"
    ]
    assert always_names.index(stop["name"]) < always_names.index(restore["name"])
    assert always_names.index(candidate_check["name"]) < always_names.index(restore["name"])
    assert always_names.index(restore["name"]) < always_names.index(verify["name"])
    assert always_names.index(verify["name"]) < always_names.index(release["name"])


def test_trial_lifecycle_lock_hold_release_and_serve_compatibility() -> None:
    serve = yaml.safe_load((ROLE / "tasks/serve.yml").read_text())
    serve_block = next(task for task in serve if "block" in task and "always" in task)
    assert serve_block["block"][0]["ansible.builtin.include_tasks"] == "hold.yml"
    assert serve_block["always"][0]["ansible.builtin.include_tasks"] == "release.yml"

    hold = yaml.safe_load((ROLE / "tasks/hold.yml").read_text())
    assert hold[0]["ansible.builtin.include_tasks"] == "lock.yml"
    assert hold[1]["ansible.builtin.include_tasks"] == "acquire-and-serve.yml"

    lock = yaml.safe_load((ROLE / "tasks/lock.yml").read_text())
    assert "llm_model_trial_lock_needed" in lock[0]["ansible.builtin.set_fact"]
    lock_block = lock[1]
    assert "llm_model_trial_lock_needed | bool" == lock_block["when"]
    assert any(task.get("name", "").startswith("Record that this run owns") for task in lock_block["block"])
    assert lock_block["block"][0]["ansible.builtin.include_tasks"] == "prepare.yml"

    assert serve[0]["ansible.builtin.include_tasks"] == "prepare.yml"
    prepare = yaml.safe_load((ROLE / "tasks/prepare.yml").read_text())
    fixed_target = [task for task in prepare if "ansible.builtin.assert" in task]
    assert "llm_model_trial_host == '6000'" in fixed_target[0]["ansible.builtin.assert"]["that"]

    release = yaml.safe_load((ROLE / "tasks/release.yml").read_text())
    assert release[0]["ansible.builtin.include_tasks"] == "restore.yml"
