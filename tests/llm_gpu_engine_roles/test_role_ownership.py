from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "roles"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text())


def test_shared_nvidia_role_has_no_engine_installation():
    shared = ROLES / "nvidia_gpu_guest"
    task_names = [task["name"].lower() for task in _yaml(shared / "tasks/main.yml")]

    assert any("nvidia userspace" in name for name in task_names)
    assert any("cuda toolkit" in name for name in task_names)
    assert not any("vllm" in name or "llama.cpp" in name for name in task_names)
    assert "llm_gpu_serving_vllm_version" not in (shared / "defaults/main/00-core.yml").read_text()
    assert "llm_gpu_serving_llamacpp_asset_regex" not in (shared / "defaults/main/00-core.yml").read_text()


def test_engine_roles_own_only_their_profiles_and_selectors():
    cases = (
        (
            "vllm_serving",
            "vllm",
            {
                "small",
                "medium-a",
                "qwen38-16k-4-auto",
                "qwen38-16k-1-auto",
                "qwen38-16k-1-fp8",
                "qwen38-16k-4-fp8",
                "qwen38-64k-1-auto",
                "qwen38-64k-1-fp8",
                "qwen38-64k-4-auto",
                "qwen38-64k-4-fp8",
                "qwen38-192k-1-auto",
                "qwen38-192k-1-fp8",
                "qwen38-192k-4-fp8",
                "qwen38-196k-4-auto",
                "qwen38-262k-1-auto",
                "qwen38-262k-1-fp8",
                "qwen38-262k-2-auto",
                "qwen38-262k-2-fp8",
                "qwen38-16k-8-auto",
                "qwen38-16k-8-fp8",
                "qwen38-64k-8-auto",
                "qwen38-64k-8-fp8",
                "qwen36-35b-a3b",
                "nemotron-super-1x8192",
                "nemotron-super-2x4096",
                "muse-glimmer-30b",
                "nemotron-lightning-30b-a3b",
            },
        ),
        ("llamacpp_serving", "llama_cpp", {"medium-b", "16gb", "max", "glm-flash"}),
    )
    for role, engine, expected_profiles in cases:
        role_root = ROLES / role
        defaults = _yaml(role_root / "defaults/main/10-profiles.yml")
        profiles = defaults[f"{role}_profiles"]

        assert "llm_gpu_active_profiles_by_engine" in defaults[f"{role}_active_profile"]
        assert set(profiles) == expected_profiles
        assert {profile["engine"] for profile in profiles.values()} == {engine}
        assert defaults[f"{role}_engine"] == engine

        main_tasks = _yaml(role_root / "tasks/main.yml")
        assert any(
            task.get("ansible.builtin.include_role", {}).get("name") == "nvidia_gpu_guest"
            for task in main_tasks
        )


def test_engine_installers_stay_separate_and_keep_shared_safety_tasks():
    vllm_install = (ROLES / "vllm_serving/tasks/install.yml").read_text()
    llama_install = (ROLES / "llamacpp_serving/tasks/install.yml").read_text()

    assert "vllm=={{ vllm_serving_version }}" in vllm_install
    assert "flashinfer-jit-cache=={{ vllm_serving_flashinfer_version }}" in vllm_install
    assert "llama-server" not in vllm_install
    assert "llama-server" in llama_install
    assert "vllm_serving_version" not in llama_install

    vllm_unit = (ROLES / "vllm_serving/templates/vllm-serving.service.j2").read_text()
    llama_unit = (ROLES / "llamacpp_serving/templates/llamacpp-serving.service.j2").read_text()
    assert "vllm serve" in vllm_unit
    assert "llama-server" not in vllm_unit
    assert "llamacpp_serving_server_bin" in llama_unit
    assert "vllm serve" not in llama_unit

    for role in ("vllm_serving", "llamacpp_serving"):
        role_root = ROLES / role
        activate = (role_root / "tasks/activate.yml").read_text()
        assert "tasks_from: cache-sync.yml" in activate
        assert (role_root / "tasks/health-check.yml").is_file()
        assert (role_root / "tasks/retire-disabled-profile.yml").is_file()
        assert (role_root / "handlers/main.yml").is_file()

    cache_sync = (ROLES / "nvidia_gpu_guest/tasks/cache-sync.yml").read_text()
    assert "Verify the local artifact against its pinned Hub revision" in cache_sync
    assert "Require an idle GPU before downloading a registered artifact" in cache_sync
