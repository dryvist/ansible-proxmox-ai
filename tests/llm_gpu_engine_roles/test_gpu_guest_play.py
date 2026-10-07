"""Contract for the GPU guest: the serving play, guest userspace, and the router switch."""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
NVIDIA_ROLE_ROOT = REPO_ROOT / "roles/nvidia_gpu_guest"
LLAMACPP_ROLE_ROOT = REPO_ROOT / "roles/llamacpp_serving"
VLLM_ROLE_ROOT = REPO_ROOT / "roles/vllm_serving"
SERVING_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving.yml"
GPU_ENGINE_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving-gpu-engines.yml"
SITE_PLAYBOOK = REPO_ROOT / "playbooks/site.yml"
USERSPACE_TASKS = NVIDIA_ROLE_ROOT / "tasks/install-nvidia-userspace.yml"
CORE_DEFAULTS = NVIDIA_ROLE_ROOT / "defaults/main/00-core.yml"
ROUTER_GROUP_VARS = REPO_ROOT / "inventory/group_vars/llm_router_group.yml"
ROUTER_DEFAULTS = REPO_ROOT / "roles/llm_router/defaults/main/20-registry.yml"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _serving_plays() -> list[dict]:
    plays = []
    for play in _load(SERVING_PLAYBOOK):
        imported = play.get("import_playbook", play.get("ansible.builtin.import_playbook"))
        if imported == GPU_ENGINE_PLAYBOOK.name:
            plays.extend(_load(GPU_ENGINE_PLAYBOOK))
        else:
            plays.append(play)
    return plays


def _engine_plays() -> dict[str, dict]:
    hosts = {
        "llm_gpu_serving_llama_cpp_group": "llamacpp_serving",
        "llm_gpu_serving_vllm_group": "vllm_serving",
    }
    plays = {
        play["hosts"]: play
        for play in _serving_plays()
        if play.get("hosts") in hosts
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


def test_serving_plays_apply_each_engine_role_to_one_guest_at_a_time() -> None:
    expected_roles = {
        "llm_gpu_serving_llama_cpp_group": "llamacpp_serving",
        "llm_gpu_serving_vllm_group": "vllm_serving",
    }

    for host_group, play in _engine_plays().items():
        assert play["serial"] == 1
        assert play["any_errors_fatal"] is True
        included = [
            task["ansible.builtin.include_role"]["name"]
            for task in _walk(play["tasks"])
            if "ansible.builtin.include_role" in task
        ]
        assert included == [expected_roles[host_group]]


def test_serving_play_precedes_the_router_pool() -> None:
    hosts = [play.get("hosts") for play in _serving_plays()]

    router = hosts.index("llm_router_group")
    for engine_group in _engine_plays():
        assert hosts.index(engine_group) < router


def test_site_reaches_the_serving_playbook_without_a_tag_filter() -> None:
    imports = [play for play in _load(SITE_PLAYBOOK) if play.get("import_playbook") == "llm-serving.yml"]

    assert len(imports) == 1
    assert "tags" not in imports[0]


def test_profile_comes_from_the_shared_selector_not_a_new_variable() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in (SERVING_PLAYBOOK, GPU_ENGINE_PLAYBOOK))
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

    engine_groups = {"llm_gpu_engine_llama_cpp_group": ["llama"], "llm_gpu_engine_vllm_group": ["vllm"]}
    assert render_profile("llama_cpp", {}) == "medium-a"
    assert render_profile("llama_cpp", engine_groups) == "medium-b"
    assert render_profile("vllm", engine_groups) == "medium-a"


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


def test_both_engine_roles_depend_on_the_shared_nvidia_guest_role() -> None:
    for role_root in (LLAMACPP_ROLE_ROOT, VLLM_ROLE_ROOT):
        includes = [
            task["ansible.builtin.include_role"]["name"]
            for task in _walk(_load(role_root / "tasks/main.yml"))
            if "ansible.builtin.include_role" in task
        ]
        assert includes == ["nvidia_gpu_guest", "nvidia_gpu_guest"]


def test_userspace_version_is_one_pinned_variable() -> None:
    defaults = _load(CORE_DEFAULTS)
    version = defaults["nvidia_gpu_guest_nvidia_userspace_version"]

    assert re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+", version)
    text = CORE_DEFAULTS.read_text(encoding="utf-8")
    pin_line = text.splitlines().index(f'nvidia_gpu_guest_nvidia_userspace_version: "{version}"')
    assert text.splitlines()[pin_line - 1].startswith("# renovate:")
    tasks = USERSPACE_TASKS.read_text(encoding="utf-8")
    assert version not in tasks


def test_userspace_install_is_userspace_only_and_pinned() -> None:
    tasks = _load(USERSPACE_TASKS)
    installs = [task for task in _walk(tasks) if "ansible.builtin.apt" in task]
    packages = yaml.safe_dump([task["ansible.builtin.apt"]["name"] for task in installs])

    assert re.search(r"dkms|kernel|nvidia-open", packages) is None
    assert installs[-1]["ansible.builtin.apt"]["install_recommends"] is False
    names = installs[-1]["ansible.builtin.apt"]["name"]
    assert all("{{ nvidia_gpu_guest_nvidia_userspace_version }}" in name for name in names)
    assert [name.split("=")[0] for name in names] == ["libcuda1", "nvidia-driver-cuda"]
    assert "nvidia-driver-pinning-" in installs[0]["ansible.builtin.apt"]["name"]


def test_userspace_install_is_skipped_when_nvidia_smi_reports_the_version() -> None:
    block = next(task for task in _load(USERSPACE_TASKS) if "block" in task)
    condition = block["when"].replace("\n", " ")
    env = jinja2.Environment()
    template = env.from_string("{{ " + condition + " }}")
    version = "595.91.07"

    def decide(rc: int, lines: list[str]) -> str:
        return template.render(
            nvidia_gpu_guest_nvidia_smi={"rc": rc, "stdout_lines": lines},
            nvidia_gpu_guest_nvidia_userspace_version=version,
        )

    assert decide(0, [version]) == "False"
    assert decide(0, [version, version]) == "False"
    assert decide(0, ["595.45.04"]) == "True"
    assert decide(9, []) == "True"
    assert decide(0, []) == "True"
    assert decide(127, []) == "True"


def test_router_projects_gpu_profiles_only_when_a_gpu_host_exists() -> None:
    expression = _load(ROUTER_GROUP_VARS)["llm_router_gpu_profiles_enabled"]
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
