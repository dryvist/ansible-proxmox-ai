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


def _profiles() -> dict:
    return yaml.safe_load(PROFILE_DEFAULTS.read_text(encoding="utf-8"))["llm_profiles"]


def _registry_profiles() -> dict:
    entries = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))["_llm_registry_gpu_pro6000"]
    return {entry["profile"]: entry for entry in entries}


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
        llm_gpu_serving_model_cache_mount_path="/cache",
        llm_gpu_serving_llamacpp_install_dir="/opt/llm-gpu-serving/llama.cpp",
        llm_gpu_serving_llamacpp_server_bin="/opt/llm-gpu-serving/llama.cpp/llama-server",
        llm_gpu_serving_profile_name=profile_name,
        llm_gpu_serving_profile=resolved_profile,
        llm_gpu_serving_profile_registry_model=registry_model,
        llm_gpu_serving_profile_model_dir=f"/cache/{registry_model['upstream_model_id']}",
    )


def _exec_start(unit: str) -> str:
    start = unit.split("ExecStart=", maxsplit=1)[1].split("\n\n[Install]", maxsplit=1)[0]
    return start.replace("\\\n", " ").strip()


def test_four_named_profiles_carry_the_serving_contract():
    profiles = _profiles()
    assert list(profiles) == ["small", "medium-a", "medium-b", "max"]
    required = {
        "engine",
        "quant",
        "linear_backend",
        "moe_backend",
        "max_model_len",
        "max_num_seqs",
        "gpu_memory_utilization",
        "enable_auto_tool_choice",
        "tool_call_parser",
        "reasoning_parser",
        "port",
    }
    assert all(required <= profile.keys() for profile in profiles.values())
    assert set(_registry_profiles()) == set(profiles)
    assert all("model_id" not in profile and "served_model_name" not in profile for profile in profiles.values())
    assert [profiles[name]["max_num_seqs"] for name in ("medium-a", "medium-b", "max")] == [8, 4, 1]


def test_each_vllm_profile_renders_its_runtime_flags():
    profiles = _profiles()
    registry_profiles = _registry_profiles()
    for name, profile in profiles.items():
        exec_start = _exec_start(_render(name, profile))
        registry_model_id = registry_profiles[name]["upstream_model_id"]
        assert f"vllm serve /cache/{registry_model_id}" in exec_start
        assert f"--served-model-name {registry_model_id}" in exec_start
        assert "--port 10434" in exec_start
        assert f"--max-model-len {profile['max_model_len']}" in exec_start
        assert f"--max-num-seqs {profile['max_num_seqs']}" in exec_start
        assert f"--gpu-memory-utilization {profile['gpu_memory_utilization']}" in exec_start
        assert "--enable-auto-tool-choice" in exec_start
        assert f"--tool-call-parser {profile['tool_call_parser']}" in exec_start
        assert f"--reasoning-parser {profile['reasoning_parser']}" in exec_start
        assert "{{" not in exec_start


def test_quantization_selects_the_expected_vllm_flag():
    profiles = _profiles()
    small = _exec_start(_render("small", profiles["small"]))
    medium = _exec_start(_render("medium-a", profiles["medium-a"]))
    assert "--dtype bfloat16" in small
    assert "--quantization modelopt" in medium
    assert "--linear-backend b12x" in medium
    assert "--moe-backend b12x" in _exec_start(_render("medium-b", profiles["medium-b"]))


def test_llama_cpp_profile_renders_its_release_binary_command():
    profile = {**_profiles()["small"], "engine": "llama_cpp", "gguf_file": "model.gguf"}
    exec_start = _exec_start(_render("small", profile))
    assert exec_start.startswith("/opt/llm-gpu-serving/llama.cpp/llama-server")
    assert "--model /cache/" in exec_start and "/model.gguf" in exec_start
    assert "--port 10434" in exec_start
    assert f"--parallel {profile['max_num_seqs']}" in exec_start
    assert "Environment=LD_LIBRARY_PATH=/opt/llm-gpu-serving/llama.cpp" in _render("small", profile)


def test_cache_sync_is_scoped_to_the_active_profile_and_reports_itemized_changes():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    cache_task = next(task for task in tasks if task.get("name", "").startswith("Sync only the active profile"))
    assert "rsync" in cache_task["ansible.builtin.command"]["argv"]
    assert "--delete" in cache_task["ansible.builtin.command"]["argv"]
    assert "--out-format=%i %n%L" in cache_task["ansible.builtin.command"]["argv"]
    assert "stdout | length > 0" in cache_task["changed_when"]
    path_task = next(task for task in tasks if task.get("name") == "Resolve active profile model paths")
    assert "llm_gpu_serving_active_registry_model.upstream_model_id" in path_task["ansible.builtin.set_fact"]["llm_gpu_serving_active_model_source"]


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
    for key in (
        "llm_gpu_serving_uv_cache_dir",
        "llm_gpu_serving_uv_python_install_dir",
        "llm_gpu_serving_uv_tool_dir",
        "llm_gpu_serving_uv_tool_bin_dir",
    ):
        assert defaults[key].startswith("{{ llm_gpu_serving_model_cache_mount_path }}")
