"""Check that engine roles own disjoint profiles and retain registry contracts."""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
NVIDIA_ROOT = REPO_ROOT / "roles/nvidia_gpu_guest"
VLLM_ROOT = REPO_ROOT / "roles/vllm_serving"
LLAMACPP_ROOT = REPO_ROOT / "roles/llamacpp_serving"
REGISTRY_FILE = REPO_ROOT / "llm-models.d/60-gpu.yml"
ARTIFACT_FILE = REPO_ROOT / "llm-models.d/65-gpu-artifacts.yml"


def _profiles() -> dict:
    vllm = yaml.safe_load((VLLM_ROOT / "defaults/main/10-profiles.yml").read_text(encoding="utf-8"))[
        "vllm_serving_profiles"
    ]
    llamacpp = yaml.safe_load(
        (LLAMACPP_ROOT / "defaults/main/10-profiles.yml").read_text(encoding="utf-8")
    )["llamacpp_serving_profiles"]
    return {**vllm, **llamacpp}


def _registry_profiles() -> dict:
    entries = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))["_llm_registry_gpu"]
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
    is_vllm = profile["engine"] == "vllm"
    role_root = VLLM_ROOT if is_vllm else LLAMACPP_ROOT
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(role_root / "templates"),
        trim_blocks=True,
        lstrip_blocks=False,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["comment"] = _comment_filter
    env.filters["mandatory"] = lambda value, message="": value
    resolved_profile = {
        **profile,
        "port": env.from_string(str(profile["port"])).render(nvidia_gpu_guest_api_port=10434),
    }
    registry_model = _registry_profiles()[profile_name]
    common = {
        "ansible_managed": "Managed by Ansible",
        "ansible_facts": {"default_ipv4": {"address": "LISTEN_ADDRESS"}},
        "nvidia_gpu_guest_user": "llm-gpu-serving",
        "nvidia_gpu_guest_group": "llm-gpu-serving",
        "nvidia_gpu_guest_data_dir": "/var/lib/llm-gpu-serving",
        "nvidia_gpu_guest_venv": "/opt/llm-gpu-serving/venv",
        "nvidia_gpu_guest_cuda_home": "/usr/local/cuda-X.Y",
        "nvidia_gpu_guest_hf_home": "HF_CACHE_HOME",
        "nvidia_gpu_guest_listen_host": "LISTEN_ADDRESS",
    }
    if is_vllm:
        template = "vllm-serving.service.j2"
        values = {
            **common,
            "vllm_serving_profile_name": profile_name,
            "vllm_serving_profile": resolved_profile,
            "vllm_serving_profile_registry_model": registry_model,
            "vllm_serving_profile_model_dir": f"/cache/models/{registry_model['artifact']['hf_repo']}",
        }
    else:
        template = "llamacpp-serving.service.j2"
        values = {
            **common,
            "llamacpp_serving_install_dir": "/opt/llm-gpu-serving/llama.cpp",
            "llamacpp_serving_server_bin": "/opt/llm-gpu-serving/llama.cpp/llama-server",
            "llamacpp_serving_profile_name": profile_name,
            "llamacpp_serving_profile": resolved_profile,
            "llamacpp_serving_profile_registry_model": registry_model,
            "llamacpp_serving_profile_model_dir": f"/cache/models/{registry_model['artifact']['hf_repo']}",
        }
    return env.get_template(template).render(**values)


def _exec_start(unit: str) -> str:
    start = unit.split("ExecStart=", maxsplit=1)[1].split("\n\n[Install]", maxsplit=1)[0]
    return start.replace("\\\n", " ").strip()


def test_named_profiles_carry_the_serving_contract():
    profiles = _profiles()
    assert list(profiles) == ["small", "medium-a", "medium-b", "16gb", "max"]
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
        "16gb": "llama_cpp",
        "max": "llama_cpp",
    }
    assert set(_registry_profiles()) == set(profiles)
    assert {name: profile["enabled"] for name, profile in profiles.items()} == {
        "small": True,
        "medium-a": True,
        "medium-b": True,
        "16gb": True,
        "max": True,
    }
    assert all("artifact_id" not in profile and "quant" not in profile for profile in profiles.values())
    assert all("model_id" not in profile and "served_model_name" not in profile for profile in profiles.values())
    assert [profiles[name]["max_num_seqs"] for name in ("medium-a", "medium-b", "16gb", "max")] == [8, 8, 1, 1]


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
        "_llm_registry_gpu"
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
        "16gb": "serving",
        "max": "serving",
    }
    assert profiles["small"]["artifact"]["engines"] == ["vllm"]
    small_artifact, small_defaults = profiles["small"]["artifact"], _profiles()["small"]
    assert small_artifact.get("tool_call_parser", small_defaults["tool_call_parser"]) == "qwen3_xml"
    assert small_artifact.get("reasoning_parser", small_defaults["reasoning_parser"]) == "qwen3"
    assert profiles["medium-a"]["artifact"]["engines"] == ["vllm"]
    assert profiles["medium-b"]["artifact"]["format"] == "GGUF"
    assert profiles["16gb"]["artifact"]["artifact_id"] == "qwen38-27b-ud-iq3-xxs"
    assert profiles["16gb"]["artifact"]["format"] == "GGUF"
    assert profiles["max"]["artifact"]["format"] == "GGUF"


def test_registry_artifact_engine_check_uses_the_runtime_profile():
    tasks = yaml.safe_load((NVIDIA_ROOT / "tasks/load-registry.yml").read_text(encoding="utf-8"))
    check = next(
        task
        for task in tasks
        if task["name"] == "Assert each serving profile resolves to exactly one artifact"
    )
    condition = check["ansible.builtin.assert"]["that"][-1]

    assert "nvidia_gpu_guest_profiles[item.key].engine" in condition


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
    for role_root in (VLLM_ROOT, LLAMACPP_ROOT):
        render_tasks = yaml.safe_load((role_root / "tasks/render-units.yml").read_text(encoding="utf-8"))
        main_tasks = yaml.safe_load((role_root / "tasks/main.yml").read_text(encoding="utf-8"))
        activate_tasks = yaml.safe_load((role_root / "tasks/activate.yml").read_text(encoding="utf-8"))
        validation_tasks = yaml.safe_load((role_root / "tasks/validate-profiles.yml").read_text(encoding="utf-8"))
        render_unit = next(task for task in render_tasks if task.get("name", "").startswith("Render one systemd"))
        profile_validation = next(
            task for task in validation_tasks if task.get("name") == "Validate every GPU serving profile"
        )
        profile_state = next(task for task in activate_tasks if task.get("name", "").startswith("Check profile service states"))
        retire = next(task for task in activate_tasks if task.get("name", "").startswith("Retire units for disabled"))
        assert any(task.get("ansible.builtin.include_tasks") == "validate-profiles.yml" for task in main_tasks)
        assert render_unit["when"] == "item.value.enabled | default(true)"
        assert profile_validation["when"] == "item.value.enabled | default(true)"
        assert profile_state["when"] == "item.value.enabled | default(true)"
        assert "not (item.value.enabled | default(true))" in retire["when"]
        retire_tasks = yaml.safe_load((role_root / "tasks/retire-disabled-profile.yml").read_text(encoding="utf-8"))
        assert any(task.get("ansible.builtin.systemd", {}).get("state") == "stopped" for task in retire_tasks)
        assert any(task.get("ansible.builtin.file", {}).get("state") == "absent" for task in retire_tasks)


def test_hf_cli_and_uv_are_pinned_and_store_tools_on_the_tofu_cache_mount():
    core_defaults = NVIDIA_ROOT / "defaults/main/00-core.yml"
    defaults = yaml.safe_load(core_defaults.read_text(encoding="utf-8"))
    tasks = yaml.safe_load((NVIDIA_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))

    uv_check = next(task for task in tasks if task.get("name") == "Read the installed uv version")
    uv_install = next(task for task in tasks if task.get("name") == "Install the pinned uv version")
    hf_install = next(task for task in tasks if task.get("name") == "Install the pinned Hugging Face CLI in the model cache")
    vllm_tasks = yaml.safe_load((VLLM_ROOT / "tasks/install.yml").read_text(encoding="utf-8"))
    vllm_defaults_path = VLLM_ROOT / "defaults/main/00-engine.yml"
    vllm_defaults = yaml.safe_load(vllm_defaults_path.read_text(encoding="utf-8"))
    venv_create = next(task for task in vllm_tasks if task.get("name") == "Create the vLLM virtual environment")
    vllm_install = next(task for task in vllm_tasks if task.get("name") == "Install the pinned vLLM build with SM120 b12x kernels")

    assert "datasource=github-releases depName=astral-sh/uv" in core_defaults.read_text(encoding="utf-8")
    assert "datasource=pypi depName=huggingface-hub" in core_defaults.read_text(encoding="utf-8")
    assert uv_check["ansible.builtin.command"]["argv"][0] == "{{ nvidia_gpu_guest_uv_bin }}"
    assert "{{ nvidia_gpu_guest_uv_version }}" in defaults["nvidia_gpu_guest_uv_install_url"]
    assert "nvidia_gpu_guest_uv_bin | dirname" in uv_install["ansible.builtin.shell"]["cmd"]
    assert hf_install["ansible.builtin.command"]["argv"][-1] == (
        "huggingface_hub=={{ nvidia_gpu_guest_huggingface_hub_version }}"
    )
    assert hf_install["environment"] == "{{ nvidia_gpu_guest_uv_environment }}"
    assert venv_create["environment"] == "{{ nvidia_gpu_guest_uv_environment }}"
    assert vllm_install["environment"] == "{{ nvidia_gpu_guest_uv_environment }}"
    assert "datasource=pypi depName=b12x" in vllm_defaults_path.read_text(encoding="utf-8")
    vllm_argv = vllm_install["ansible.builtin.command"]["argv"]
    assert "vllm=={{ vllm_serving_version }}" in vllm_argv
    assert f"b12x=={{{{ vllm_serving_b12x_version }}}}" in vllm_argv
    assert vllm_defaults["vllm_serving_b12x_version"] == "1.5.0"
    assert not any("[b12x]" in str(arg) for arg in vllm_argv)
    for key in (
        "nvidia_gpu_guest_uv_cache_dir",
        "nvidia_gpu_guest_uv_python_install_dir",
        "nvidia_gpu_guest_uv_tool_dir",
        "nvidia_gpu_guest_uv_tool_bin_dir",
    ):
        assert defaults[key].startswith("{{ nvidia_gpu_guest_model_cache_mount_path }}")
