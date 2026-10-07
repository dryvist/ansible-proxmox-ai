"""Check the llama_cpp engine path: GGUF profiles render a llama-server unit from profile and registry fields."""

from __future__ import annotations

import fnmatch
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/llamacpp_serving"
PROFILE_DEFAULTS = ROLE_ROOT / "defaults/main/10-profiles.yml"
TEMPLATE_DIR = ROLE_ROOT / "templates"
REGISTRY_FILE = REPO_ROOT / "llm-models.d/60-gpu-pro6000.yml"
ARTIFACT_FILES = (
    REPO_ROOT / "llm-models.d/65-gpu-pro6000-artifacts.yml",
    REPO_ROOT / "llm-models.d/66-gpu-pro6000-artifacts-glm53flash.yml",
)
LLAMA_BIN = "/opt/llm-gpu-serving/llama.cpp/llama-server"


def _profiles() -> dict:
    return yaml.safe_load(PROFILE_DEFAULTS.read_text(encoding="utf-8"))["llamacpp_serving_profiles"]


def _llama_profiles() -> dict:
    return {name: profile for name, profile in _profiles().items() if profile["engine"] == "llama_cpp"}


def _artifacts() -> list[dict]:
    return [
        artifact
        for path in ARTIFACT_FILES
        for key, entries in yaml.safe_load(path.read_text(encoding="utf-8")).items()
        if key.startswith("_llm_model_artifacts")
        for artifact in entries
    ]


def _registry(profile_name: str) -> dict:
    entries = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))["_llm_registry_gpu_pro6000"]
    artifacts = _artifacts()
    by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
    entry = next(item for item in entries if item["profile"] == profile_name)
    artifact = by_id[entry["artifact_id"]]
    return {**entry, "artifact": artifact, "upstream_model_id": artifact["hf_repo"]}


def _render(profile_name: str, profile: dict) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
        trim_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["comment"] = lambda text: "# " + str(text)
    env.filters["mandatory"] = lambda value, message="": value
    registry = _registry(profile_name)
    return env.get_template("llamacpp-serving.service.j2").render(
        ansible_managed="Managed by Ansible",
        ansible_facts={"default_ipv4": {"address": "LISTEN_ADDRESS"}},
        nvidia_gpu_guest_user="llm-gpu-serving",
        nvidia_gpu_guest_group="llm-gpu-serving",
        nvidia_gpu_guest_data_dir="/var/lib/llm-gpu-serving",
        nvidia_gpu_guest_hf_home="HF_CACHE_HOME",
        nvidia_gpu_guest_listen_host="LISTEN_ADDRESS",
        llamacpp_serving_server_bin=LLAMA_BIN,
        llamacpp_serving_install_dir="/opt/llm-gpu-serving/llama.cpp",
        llamacpp_serving_profile_name=profile_name,
        llamacpp_serving_profile={**profile, "port": 10434},
        llamacpp_serving_profile_registry_model=registry,
        llamacpp_serving_profile_model_dir=f"/cache/models/{registry['artifact']['hf_repo']}",
    )


def _exec_start(unit: str) -> str:
    start = unit.split("ExecStart=", maxsplit=1)[1].split("[Install]", maxsplit=1)[0]
    return " ".join(start.replace("\\\n", " ").split())


def test_every_gguf_profile_renders_a_llama_server_command_from_profile_and_registry_fields():
    assert set(_llama_profiles()) == {"medium-b", "max", "glm-flash"}
    for name, profile in _llama_profiles().items():
        unit = _render(name, profile)
        exec_start = _exec_start(unit)
        registry = _registry(name)
        artifact = registry["artifact"]
        total_context = profile["max_model_len"] * profile["max_num_seqs"]
        assert exec_start.startswith(LLAMA_BIN + " --model ")
        assert f"--model /cache/models/{artifact['hf_repo']}/{artifact['gguf_file']}" in exec_start
        assert f"--alias {registry['upstream_model_id']}" in exec_start
        assert "--host LISTEN_ADDRESS" in exec_start
        assert "--port 10434" in exec_start
        assert f"--ctx-size {total_context}" in exec_start
        assert f"--parallel {profile['max_num_seqs']}" in exec_start
        assert f"--n-gpu-layers {profile['gpu_layers']}" in exec_start
        assert "--flash-attn on" in exec_start
        assert exec_start.endswith("--jinja")
        assert "vllm" not in exec_start
        assert "--cache-type-k" not in exec_start
        assert "{{" not in unit
        assert "Environment=LD_LIBRARY_PATH=/opt/llm-gpu-serving/llama.cpp" in unit
        assert "[Install]\nWantedBy=multi-user.target" in unit


def test_total_context_is_per_agent_context_times_parallel_slots():
    contexts = {
        name: int(_exec_start(_render(name, profile)).split("--ctx-size ")[1].split()[0])
        for name, profile in _llama_profiles().items()
    }
    expected = {
        name: profile["max_model_len"] * profile["max_num_seqs"] for name, profile in _llama_profiles().items()
    }
    assert contexts == expected
    assert contexts["medium-b"] == 8 * _profiles()["medium-b"]["max_model_len"]


def test_optional_profile_fields_become_flags():
    profile = {**_llama_profiles()["medium-b"], "kv_cache_dtype": "q8_0", "reasoning_format": "none"}
    exec_start = _exec_start(_render("medium-b", profile))
    assert "--cache-type-k q8_0 --cache-type-v q8_0" in exec_start
    assert "--reasoning-format none" in exec_start
    assert "--flash-attn on" in exec_start
    off = _exec_start(_render("medium-b", {**profile, "flash_attention": False}))
    assert "--flash-attn" not in off
    extra = _exec_start(_render("max", _llama_profiles()["max"]))
    assert "-lm mmap -lzm on" in extra


def test_each_served_gguf_artifact_names_one_literal_file_its_selector_downloads():
    for name in _llama_profiles():
        artifact = _registry(name)["artifact"]
        assert artifact["format"] == "GGUF"
        assert "llama_cpp" in artifact["engines"]
        assert artifact["use"] == "serving"
        gguf_file = artifact["gguf_file"]
        assert gguf_file.endswith(".gguf")
        assert not any(character in gguf_file for character in "*?[")
        assert any(fnmatch.fnmatch(gguf_file, glob) for glob in artifact["include_globs"])


def test_registry_entries_stay_inactive_while_every_profile_is_selectable():
    assert all(profile["enabled"] is True for profile in _profiles().values())
    entries = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))["_llm_registry_gpu_pro6000"]
    assert all(entry["enabled"] is False and entry["servable"] is False for entry in entries)


def test_role_validation_requires_a_literal_gguf_file_for_llama_cpp_profiles():
    main_tasks = yaml.safe_load((ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    validation_tasks = yaml.safe_load((ROLE_ROOT / "tasks/validate-profiles.yml").read_text(encoding="utf-8"))
    validation = next(task for task in validation_tasks if task.get("name") == "Validate every GPU serving profile")
    assert any(task.get("ansible.builtin.include_tasks") == "validate-profiles.yml" for task in main_tasks)
    conditions = "\n".join(validation["ansible.builtin.assert"]["that"])
    assert "artifact.gguf_file" in conditions
    assert "artifact.format == 'GGUF'" in conditions
