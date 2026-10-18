"""Check pinned installation tools remain on the model-cache mount."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
NVIDIA_ROOT = REPO_ROOT / "roles/nvidia_gpu_guest"
VLLM_ROOT = REPO_ROOT / "roles/vllm_serving"


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
    assert vllm_defaults["vllm_serving_version"] == "0.32.0"
    assert vllm_defaults["vllm_serving_b12x_version"] == "1.5.1"
    assert not any("[b12x]" in str(arg) for arg in vllm_argv)
    for key in (
        "nvidia_gpu_guest_uv_cache_dir",
        "nvidia_gpu_guest_uv_python_install_dir",
        "nvidia_gpu_guest_uv_tool_dir",
        "nvidia_gpu_guest_uv_tool_bin_dir",
    ):
        assert defaults[key].startswith("{{ nvidia_gpu_guest_model_cache_mount_path }}")
