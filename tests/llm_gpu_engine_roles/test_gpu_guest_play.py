"""Contract for the GPU guest: the serving play, guest userspace, and the router switch."""

from __future__ import annotations

import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
NVIDIA_ROLE_ROOT = REPO_ROOT / "roles/nvidia_gpu_guest"
LLAMACPP_ROLE_ROOT = REPO_ROOT / "roles/llamacpp_serving"
VLLM_ROLE_ROOT = REPO_ROOT / "roles/vllm_serving"
SGLANG_ROLE_ROOT = REPO_ROOT / "roles/sglang_serving"
SERVING_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving.yml"
LLAMACPP_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving-llama-cpp.yml"
GPU_ENGINE_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving-gpu-engines.yml"
SITE_PLAYBOOK = REPO_ROOT / "playbooks/site.yml"
LLM_GPU_SERVING_ROLE_ROOT = REPO_ROOT / "roles/llm_gpu_serving"
ROUTER_GPU_PROFILE_VARS = REPO_ROOT / "inventory/group_vars/all.yml"
ROUTER_DEFAULTS = REPO_ROOT / "roles/llm_router/defaults/main/20-registry.yml"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _serving_plays() -> list[dict]:
    plays = []
    for play in _load(SERVING_PLAYBOOK):
        imported = play.get("import_playbook", play.get("ansible.builtin.import_playbook"))
        if imported == LLAMACPP_PLAYBOOK.name:
            plays.extend(_load(LLAMACPP_PLAYBOOK))
        elif imported == GPU_ENGINE_PLAYBOOK.name:
            plays.extend(_load(GPU_ENGINE_PLAYBOOK))
        else:
            plays.append(play)
    return plays


def _host_group(play: dict) -> str:
    return play["hosts"].split(":&", maxsplit=1)[0]


def _engine_plays() -> dict[str, dict]:
    hosts = {
        "llm_gpu_serving_llama_cpp_group": "llamacpp_serving",
        "llm_gpu_serving_vllm_group": "vllm_serving",
        "llm_gpu_serving_sglang_group": "sglang_serving",
        "llm_gpu_serving_strata_group": "strata_serving",
    }
    plays = {
        _host_group(play): play
        for play in _serving_plays()
        if _host_group(play) in hosts
    }
    assert set(plays) == set(hosts), "both engine-specific plays must exist"
    return plays


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_serving_plays_apply_the_selected_engine_role_to_one_guest_at_a_time() -> None:
    expected_roles = {
        "llm_gpu_serving_llama_cpp_group": ["llama_cpp", "llm_gpu_serving", "llamacpp_serving"],
        "llm_gpu_serving_vllm_group": ["vllm_serving"],
        "llm_gpu_serving_sglang_group": ["sglang_serving"],
        "llm_gpu_serving_strata_group": ["strata_serving"],
    }

    for host_group, play in _engine_plays().items():
        assert play["hosts"] == host_group
        assert play["serial"] == 1
        assert play["any_errors_fatal"] is True
        included = [
            task["ansible.builtin.include_role"]["name"]
            for task in _walk(play["tasks"])
            if "ansible.builtin.include_role" in task
        ]
        assert included == expected_roles[host_group]
        if host_group == "llm_gpu_serving_llama_cpp_group":
            assert "llama_cpp" in play["tags"]
            bolt = next(
                task for task in play["tasks"]
                if task["name"] == "Converge shared llama.cpp profiles through the selector"
            )
            specialized = next(
                task for task in play["tasks"]
                if task["name"].startswith("Converge llamacpp_serving")
            )
            shared_profiles = "llm_gpu_shared_llamacpp_profiles"
            assert play["vars"][shared_profiles] == [
                "nemotron-bolt-64k-16",
                "nemotron-bolt-256k-24",
                "qwen38-27b-131k-1",
                "moe1-qwen36-n14-s1",
                "moe1-qwen36-n16-s2",
                "moe1-bolt-n38-s1",
                "moe1-bolt-n40-s2",
            ]
            assert bolt["when"] == f"llm_active_profile in {shared_profiles}"
            assert specialized["when"] == f"llm_active_profile not in {shared_profiles}"


def test_serving_play_precedes_the_router_pool() -> None:
    hosts = [_host_group(play) for play in _serving_plays()]

    router = hosts.index("llm_router_group")
    for engine_group in _engine_plays():
        assert hosts.index(engine_group) < router


def test_site_reaches_the_serving_playbook_without_a_tag_filter() -> None:
    imports = [play for play in _load(SITE_PLAYBOOK) if play.get("import_playbook") == "llm-serving.yml"]

    assert len(imports) == 1
    assert "tags" not in imports[0]


def test_profile_comes_from_the_shared_selector_not_a_new_variable() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SERVING_PLAYBOOK, LLAMACPP_PLAYBOOK, GPU_ENGINE_PLAYBOOK)
    )
    all_vars = _load(REPO_ROOT / "inventory/group_vars/all.yml")
    selector_expression = all_vars["llm_active_profile"]

    assert "hostvars['localhost']['tofu_data']" in selector_expression
    assert ".get('llm_gpu_engine', 'llama_cpp')" in selector_expression
    assert all("llm_gpu_engine" not in play.get("vars", {}) for play in _engine_plays().values())
    assert "llm_gpu_serving_model_cache_mount_path" not in text
    assert "llm_gpu_serving_model_origin_mount_path" not in text

    template = jinja2.Environment().from_string(selector_expression)

    def render_profile(selector: str, groups: dict) -> str:
        return template.render(
            hostvars={"localhost": {"tofu_data": {"llm_gpu_engine": selector}}},
            groups=groups,
            llm_gpu_active_profiles_by_engine=all_vars["llm_gpu_active_profiles_by_engine"],
        )

    engine_groups = {
        "llm_gpu_engine_llama_cpp_group": ["llama"],
        "llm_gpu_engine_vllm_group": ["vllm"],
        "llm_gpu_engine_sglang_group": ["sglang"],
    }
    assert render_profile("llama_cpp", {}) == "medium-a"
    assert render_profile("llama_cpp", engine_groups) == "medium-b"
    assert render_profile("vllm", engine_groups) == "medium-a"
    assert render_profile("sglang", engine_groups) == all_vars["llm_gpu_active_profiles_by_engine"]["sglang"]
    assert render_profile("strata", engine_groups) == "medium-a"


def test_no_play_gives_the_gpu_guest_a_container_engine() -> None:
    # LiveCodeBench generations run in a local subprocess with a per-test
    # timeout unless the recipe requests a Docker sandbox, so the guest carries
    # no engine. A second mechanism for the same job is not added.
    assert all("docker_engine" not in yaml.safe_dump(play) for play in _engine_plays().values())
    site_imports = [play for play in _load(SITE_PLAYBOOK) if play.get("import_playbook") == "llm-serving.yml"]
    assert len(site_imports) == 1
    assert "docker_engine" not in yaml.safe_dump(site_imports[0])
    assert "docker_engine" not in yaml.safe_dump(_load(LLAMACPP_ROLE_ROOT / "tasks/main.yml"))
    assert "docker_engine" not in yaml.safe_dump(_load(VLLM_ROLE_ROOT / "tasks/main.yml"))
    assert "docker_engine" not in yaml.safe_dump(_load(SGLANG_ROLE_ROOT / "tasks/main.yml"))
    assert "docker_engine" not in yaml.safe_dump(_load(REPO_ROOT / "roles/strata_serving/tasks/main.yml"))


def test_engine_roles_depend_on_the_shared_nvidia_guest_role() -> None:
    for role_root in (LLAMACPP_ROLE_ROOT, VLLM_ROLE_ROOT, SGLANG_ROLE_ROOT, REPO_ROOT / "roles/strata_serving"):
        includes = [
            task["ansible.builtin.include_role"]["name"]
            for task in _walk(_load(role_root / "tasks/main.yml"))
            if "ansible.builtin.include_role" in task
        ]
        assert includes == ["nvidia_gpu_guest", "nvidia_gpu_guest"]

    context = _load(NVIDIA_ROLE_ROOT / "defaults/main/10-engine-context.yml")
    assert "sglang_serving_engine" in context["nvidia_gpu_guest_engine"]
    assert "sglang_serving_profiles" in context["nvidia_gpu_guest_profiles"]
    assert "sglang_serving_active_profile" in context["nvidia_gpu_guest_active_profile"]


def test_legacy_gpu_serving_role_uses_the_shared_userspace_installer() -> None:
    tasks = _load(LLM_GPU_SERVING_ROLE_ROOT / "tasks/main.yml")
    includes = [
        task["ansible.builtin.include_role"]
        for task in tasks
        if task.get("name") == "Install NVIDIA userspace from the host module version"
    ]

    assert includes == [{"name": "nvidia_gpu_guest", "tasks_from": "install-nvidia-userspace.yml"}]


def test_legacy_serving_play_installs_fan_control_only_when_enabled() -> None:
    play = next(
        play
        for play in _serving_plays()
        if _host_group(play) == "llm_gpu_legacy_group"
    )
    fan_control = next(
        task
        for task in play["tasks"]
        if task.get("name") == "Install GPU fan control for opted-in legacy guests"
    )

    assert "llm_gpu_serving" in play["tags"]
    assert fan_control["ansible.builtin.include_role"] == {
        "name": "nvidia_gpu_guest",
        "tasks_from": "install-fan-control.yml",
    }
    assert fan_control["when"] == "nvidia_gpu_guest_fan_control_enabled | default(false) | bool"


def test_router_projects_gpu_profiles_only_when_a_gpu_host_exists() -> None:
    expression = _load(ROUTER_GPU_PROFILE_VARS)["llm_router_gpu_profiles_enabled"]
    env = jinja2.Environment()

    def render(groups: dict) -> str:
        return env.from_string(expression).render(groups=groups)

    assert render({}) == "False"
    assert render({"llm_gpu_group": []}) == "False"
    assert render({"llm_gpu_group": ["gpu-guest"]}) == "True"
    assert _load(ROUTER_DEFAULTS)["llm_router_gpu_profiles_enabled"] is False


def test_campaign_tools_are_linked_onto_the_default_path_after_install() -> None:
    tasks = _load(VLLM_ROLE_ROOT / "tasks/install.yml")
    names = [task["name"] for task in tasks]
    link = tasks[names.index("Put the vLLM and Hugging Face CLIs on the default PATH")]

    assert link["ansible.builtin.file"]["state"] == "link"
    assert {item["name"] for item in link["loop"]} == {"vllm", "hf"}
    install_index = names.index("Install the pinned vLLM build with SM120 b12x kernels")
    assert names.index(link["name"]) > install_index
    assert "Install the pinned Hugging Face CLI" not in " ".join(names)


@pytest.mark.parametrize(
    "engine_group",
    [
        "llm_gpu_legacy_group",
        "llm_gpu_serving_llama_cpp_group",
        "llm_gpu_serving_vllm_group",
    ],
)
def test_engine_host_outside_nvidia_gpu_group_fails_its_play(tmp_path: Path, engine_group: str) -> None:
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(yaml.safe_dump({
        "all": {
            "children": {
                engine_group: {"hosts": {"non-gpu-guest": {"ansible_connection": "local"}}},
                "nvidia_gpu_group": {"hosts": {}},
            },
        },
    }), encoding="utf-8")
    result = subprocess.run(
        ["ansible-playbook", "-i", str(inventory), "-e", "ansible_become=false", str(GPU_ENGINE_PLAYBOOK)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "not in nvidia_gpu_group" in output, output
    assert "TASK [Include " not in output, output
