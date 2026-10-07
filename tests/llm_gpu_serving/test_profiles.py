"""Check the GPU serving profile defaults and render each systemd unit."""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/llm_gpu_serving"
PROFILE_DEFAULTS = ROLE_ROOT / "defaults/main/10-profiles.yml"
UNIT_TEMPLATE = ROLE_ROOT / "templates/llm-gpu-serving.service.j2"
REGISTRY_FILE = REPO_ROOT / "llm-models.d/60-gpu-pro6000.yml"
ARTIFACT_FILE = REPO_ROOT / "llm-models.d/65-gpu-pro6000-artifacts.yml"


def _profiles() -> dict:
    return yaml.safe_load(PROFILE_DEFAULTS.read_text(encoding="utf-8"))["llm_profiles"]


def _registry_profiles() -> dict:
    entries = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))["_llm_registry_gpu_pro6000"]
    artifacts = yaml.safe_load(ARTIFACT_FILE.read_text(encoding="utf-8"))["_llm_model_artifacts"]
    by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
    return {
        entry["profile"]: {
            **entry,
            "artifact": by_id[entry["artifact_id"]],
            "upstream_model_id": by_id[entry["artifact_id"]]["hf_repo"],
        }
        for entry in entries
    }


def _comment_filter(text: str) -> str:
    return "\n".join(f"# {line}" if line else "#" for line in str(text).splitlines())


def _render(profile_name: str, profile: dict) -> str:
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=False, undefined=jinja2.StrictUndefined)
    env.filters["comment"] = _comment_filter
    env.filters["mandatory"] = lambda value, message="": value
    resolved_profile = {
        **profile,
        "port": env.from_string(str(profile["port"])).render(llm_gpu_serving_api_port=10434),
    }
    registry_model = _registry_profiles()[profile_name]
    return env.from_string(UNIT_TEMPLATE.read_text(encoding="utf-8")).render(
        ansible_managed="Managed by Ansible",
        ansible_facts={"default_ipv4": {"address": "LISTEN_ADDRESS"}},
        llm_gpu_serving_user="llm-gpu-serving",
        llm_gpu_serving_group="llm-gpu-serving",
        llm_gpu_serving_data_dir="/var/lib/llm-gpu-serving",
        llm_gpu_serving_venv="/opt/llm-gpu-serving/venv",
        llm_gpu_serving_cuda_home="/usr/local/cuda-X.Y",
        llm_gpu_serving_model_cache_mount_path="/cache",
        llm_gpu_serving_hf_home="HF_CACHE_HOME",
        llm_gpu_serving_llamacpp_install_dir="/opt/llm-gpu-serving/llama.cpp",
        llm_gpu_serving_llamacpp_server_bin="/opt/llm-gpu-serving/llama.cpp/llama-server",
        llm_gpu_serving_profile_name=profile_name,
        llm_gpu_serving_profile=resolved_profile,
        llm_gpu_serving_profile_registry_model=registry_model,
        llm_gpu_serving_profile_model_dir=f"/cache/models/{registry_model['artifact']['hf_repo']}",
    )


def _exec_start(unit: str) -> str:
    start = unit.split("ExecStart=", maxsplit=1)[1].split("\n\n[Install]", maxsplit=1)[0]
    return start.replace("\\\n", " ").strip()


def test_four_named_profiles_carry_the_serving_contract():
    profiles = _profiles()
    assert list(profiles) == ["small", "medium-a", "medium-b", "max"]
    common = {"engine", "max_model_len", "max_num_seqs", "port"}
    vllm_only = {
        "linear_backend",
        "moe_backend",
        "gpu_memory_utilization",
        "enable_auto_tool_choice",
        "tool_call_parser",
        "reasoning_parser",
    }
    assert all(common <= profile.keys() for profile in profiles.values())
    assert all(
        vllm_only <= profile.keys() for profile in profiles.values() if profile["engine"] == "vllm"
    )
    assert {name: profile["engine"] for name, profile in profiles.items()} == {
        "small": "vllm",
        "medium-a": "vllm",
        "medium-b": "llama_cpp",
        "max": "llama_cpp",
    }
    assert set(_registry_profiles()) == set(profiles)
    assert {name: profile["enabled"] for name, profile in profiles.items()} == {
        "small": True,
        "medium-a": True,
        "medium-b": True,
        "max": True,
    }
    assert all("artifact_id" not in profile and "quant" not in profile for profile in profiles.values())
    assert all("model_id" not in profile and "served_model_name" not in profile for profile in profiles.values())
    assert [profiles[name]["max_num_seqs"] for name in ("medium-a", "medium-b", "max")] == [8, 8, 1]


def test_artifact_registry_is_the_only_source_for_model_files_and_quantization():
    artifact_file = yaml.safe_load(ARTIFACT_FILE.read_text(encoding="utf-8"))
    artifacts = artifact_file["_llm_model_artifacts"]
    artifact_ids = [artifact["artifact_id"] for artifact in artifacts]
    assert len(artifact_ids) == len(set(artifact_ids))

    required = {
        "artifact_id",
        "hf_repo",
        "include_globs",
        "format",
        "quantization",
        "engines",
        "use",
    }
    assert all(required <= artifact.keys() for artifact in artifacts)
    assert all(artifact["include_globs"] for artifact in artifacts)
    assert all(artifact["use"] in {"serving", "benchmark-only"} for artifact in artifacts)

    registry_entries = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))[
        "_llm_registry_gpu_pro6000"
    ]
    assert all("artifact_id" in entry for entry in registry_entries)
    assert all(
        not {"hf_repo", "include_globs", "format", "quantization"} & entry.keys()
        for entry in registry_entries
    )
    assert all(
        not {"quant", "gguf_file"} & profile.keys() for profile in _profiles().values()
    )

    profiles = _registry_profiles()
    assert profiles["small"]["artifact_id"] == "qwen35-9b-nvfp4"
    assert {name: model["artifact"]["use"] for name, model in profiles.items()} == {
        "small": "serving",
        "medium-a": "serving",
        "medium-b": "serving",
        "max": "serving",
    }
    assert profiles["small"]["artifact"]["engines"] == ["vllm"]
    small_artifact, small_defaults = profiles["small"]["artifact"], _profiles()["small"]
    assert small_artifact.get("tool_call_parser", small_defaults["tool_call_parser"]) == "qwen3_xml"
    assert small_artifact.get("reasoning_parser", small_defaults["reasoning_parser"]) == "qwen3"
    assert profiles["medium-a"]["artifact"]["engines"] == ["vllm"]
    assert profiles["medium-b"]["artifact"]["format"] == "GGUF"
    assert profiles["max"]["artifact"]["format"] == "GGUF"


def test_registry_artifact_engine_check_uses_the_runtime_profile():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/load-registry.yml").read_text(encoding="utf-8"))
    check = next(
        task
        for task in tasks
        if task["name"] == "Assert each serving profile resolves to exactly one artifact"
    )
    condition = check["ansible.builtin.assert"]["that"][-1]

    assert "llm_profiles[item.key].engine" in condition


def test_each_vllm_profile_renders_its_runtime_flags():
    profiles = _profiles()
    registry_profiles = _registry_profiles()
    for name in ("small", "medium-a"):
        profile = profiles[name]
        exec_start = _exec_start(_render(name, profile))
        registry_model_id = registry_profiles[name]["upstream_model_id"]
        artifact = registry_profiles[name]["artifact"]
        tool_parser = artifact.get("tool_call_parser", profile["tool_call_parser"])
        reasoning_parser = artifact.get("reasoning_parser", profile["reasoning_parser"])
        assert f"vllm serve /cache/models/{registry_model_id}" in exec_start
        assert f"--served-model-name {registry_model_id}" in exec_start
        assert "--port 10434" in exec_start
        assert f"--max-model-len {profile['max_model_len']}" in exec_start
        assert f"--max-num-seqs {profile['max_num_seqs']}" in exec_start
        assert f"--gpu-memory-utilization {profile['gpu_memory_utilization']}" in exec_start
        assert "--enable-auto-tool-choice" in exec_start
        assert f"--tool-call-parser {tool_parser}" in exec_start
        assert f"--reasoning-parser {reasoning_parser}" in exec_start
        assert "{{" not in exec_start


def test_nvfp4_profiles_leave_quantization_to_the_checkpoint():
    profiles = _profiles()
    small = _exec_start(_render("small", profiles["small"]))
    medium = _exec_start(_render("medium-a", profiles["medium-a"]))
    assert "--quantization" not in small
    assert "--quantization" not in medium
    assert "--tool-call-parser qwen3_xml" in small
    assert "--reasoning-parser qwen3" in small
    assert "--linear-backend b12x" in medium
    assert "--moe-backend b12x" not in medium


def test_only_enabled_profiles_are_rendered_and_checked_by_the_role():
    render_tasks = yaml.safe_load((ROLE_ROOT / "tasks/render-units.yml").read_text(encoding="utf-8"))
    main_tasks = yaml.safe_load((ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    validation_tasks = yaml.safe_load((ROLE_ROOT / "tasks/validate-profiles.yml").read_text(encoding="utf-8"))
    render_unit = next(task for task in render_tasks if task.get("name", "").startswith("Render one systemd"))
    profile_validation = next(
        task for task in validation_tasks if task.get("name") == "Validate every GPU serving profile"
    )
    profile_state = next(task for task in main_tasks if task.get("name", "").startswith("Check profile service states"))
    retire = next(task for task in main_tasks if task.get("name", "").startswith("Retire units for disabled"))
    assert any(task.get("ansible.builtin.include_tasks") == "validate-profiles.yml" for task in main_tasks)
    assert render_unit["when"] == "item.value.enabled | default(true)"
    assert profile_validation["when"] == "item.value.enabled | default(true)"
    assert profile_state["when"] == "item.value.enabled | default(true)"
    assert "not (item.value.enabled | default(true))" in retire["when"]
    retire_tasks = yaml.safe_load((ROLE_ROOT / "tasks/retire-disabled-profile.yml").read_text(encoding="utf-8"))
    assert any(task.get("ansible.builtin.systemd", {}).get("state") == "stopped" for task in retire_tasks)
    assert any(task.get("ansible.builtin.file", {}).get("state") == "absent" for task in retire_tasks)


def test_hf_cli_and_uv_are_pinned_and_store_tools_on_the_tofu_cache_mount():
    core_defaults = ROLE_ROOT / "defaults/main/00-core.yml"
    defaults = yaml.safe_load(core_defaults.read_text(encoding="utf-8"))
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))

    uv_check = next(task for task in tasks if task.get("name") == "Read the installed uv version")
    uv_install = next(task for task in tasks if task.get("name") == "Install the pinned uv version")
    hf_install = next(task for task in tasks if task.get("name") == "Install the pinned Hugging Face CLI in the model cache")
    venv_create = next(task for task in tasks if task.get("name") == "Create the vLLM virtual environment")
    vllm_install = next(task for task in tasks if task.get("name") == "Install the pinned vLLM build with SM120 b12x kernels")

    assert "datasource=github-releases depName=astral-sh/uv" in core_defaults.read_text(encoding="utf-8")
    assert "datasource=pypi depName=huggingface-hub" in core_defaults.read_text(encoding="utf-8")
    assert uv_check["ansible.builtin.command"]["argv"][0] == "{{ llm_gpu_serving_uv_bin }}"
    assert "{{ llm_gpu_serving_uv_version }}" in defaults["llm_gpu_serving_uv_install_url"]
    assert "llm_gpu_serving_uv_bin | dirname" in uv_install["ansible.builtin.shell"]["cmd"]
    assert hf_install["ansible.builtin.command"]["argv"][-1] == (
        "huggingface_hub=={{ llm_gpu_serving_huggingface_hub_version }}"
    )
    assert hf_install["environment"] == "{{ llm_gpu_serving_uv_environment }}"
    assert venv_create["environment"] == "{{ llm_gpu_serving_uv_environment }}"
    assert vllm_install["environment"] == "{{ llm_gpu_serving_uv_environment }}"
    assert "datasource=pypi depName=b12x" in core_defaults.read_text(encoding="utf-8")
    vllm_argv = vllm_install["ansible.builtin.command"]["argv"]
    assert "vllm=={{ llm_gpu_serving_vllm_version }}" in vllm_argv
    assert "b12x=={{ llm_gpu_serving_b12x_version }}" in vllm_argv
    assert not any("[b12x]" in str(arg) for arg in vllm_argv)
    for key in (
        "llm_gpu_serving_uv_cache_dir",
        "llm_gpu_serving_uv_python_install_dir",
        "llm_gpu_serving_uv_tool_dir",
        "llm_gpu_serving_uv_tool_bin_dir",
    ):
        assert defaults[key].startswith("{{ llm_gpu_serving_model_cache_mount_path }}")


def test_model_campaign_uses_target_endpoint_and_cache_parameters():
    campaign_path = REPO_ROOT / "playbooks/llm-model-campaign.yml"
    campaign = campaign_path.read_text(encoding="utf-8")
    target_playbook = (REPO_ROOT / "playbooks/llm-model-campaign-target.yml").read_text(
        encoding="utf-8"
    )

    assert "ansible.builtin.import_playbook: llm-model-campaign-target.yml" in campaign
    campaign += target_playbook
    assert "benchmark_endpoint_root is defined" in campaign
    assert "benchmark_cache_path is defined" in campaign
    assert 'hosts: "{{ machine | default(\'localhost\') }}"' in campaign
    assert "engine == 'mlx_lm'" in campaign
    assert "engine != 'mlx_lm'" in campaign
