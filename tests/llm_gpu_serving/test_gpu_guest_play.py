"""Contract for the GPU guest: the serving play, guest userspace, and the router switch."""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/llm_gpu_serving"
SERVING_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving.yml"
SITE_PLAYBOOK = REPO_ROOT / "playbooks/site.yml"
USERSPACE_TASKS = ROLE_ROOT / "tasks/install-nvidia-userspace.yml"
CORE_DEFAULTS = ROLE_ROOT / "defaults/main/00-core.yml"
ROUTER_GROUP_VARS = REPO_ROOT / "inventory/group_vars/llm_router_group.yml"
ROUTER_DEFAULTS = REPO_ROOT / "roles/llm_router/defaults/main/20-registry.yml"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _gpu_play() -> dict:
    plays = [play for play in _load(SERVING_PLAYBOOK) if play.get("hosts") == "llm_gpu_group"]
    assert len(plays) == 1, "exactly one play must serve llm_gpu_group"
    return plays[0]


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_serving_play_applies_the_role_to_the_gpu_group_one_host_at_a_time() -> None:
    play = _gpu_play()

    assert play["serial"] == 1
    assert "llm_gpu_serving" in play["tags"]
    included = [
        task["ansible.builtin.include_role"]["name"]
        for task in _walk(play["tasks"])
        if "ansible.builtin.include_role" in task
    ]
    assert included == ["llm_gpu_serving"]


def test_serving_play_precedes_the_router_pool() -> None:
    hosts = [play.get("hosts") for play in _load(SERVING_PLAYBOOK)]

    assert hosts.index("llm_gpu_group") < hosts.index("llm_router_group")


def test_site_reaches_the_serving_playbook_without_a_tag_filter() -> None:
    imports = [play for play in _load(SITE_PLAYBOOK) if play.get("import_playbook") == "llm-serving.yml"]

    assert len(imports) == 1
    assert "tags" not in imports[0]


def test_profile_comes_from_the_shared_selector_not_a_new_variable() -> None:
    text = SERVING_PLAYBOOK.read_text(encoding="utf-8")

    assert "llm_active_profile" not in _gpu_play().get("vars", {})
    assert "llm_gpu_serving_model_cache_mount_path" not in text
    assert "llm_gpu_serving_model_origin_mount_path" not in text


def test_no_play_gives_the_gpu_guest_a_container_engine() -> None:
    # LiveCodeBench generations run in a local subprocess with a per-test
    # timeout unless the recipe requests a Docker sandbox, so the guest carries
    # no engine. A second mechanism for the same job is not added.
    for path in (SERVING_PLAYBOOK, SITE_PLAYBOOK):
        for play in _load(path):
            if play.get("hosts") == "llm_gpu_group":
                assert "docker_engine" not in yaml.safe_dump(play)
    assert "docker_engine" not in yaml.safe_dump(_load(ROLE_ROOT / "tasks/main.yml"))


def test_userspace_version_is_one_pinned_variable() -> None:
    defaults = _load(CORE_DEFAULTS)
    version = defaults["llm_gpu_serving_nvidia_userspace_version"]

    assert re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+", version)
    text = CORE_DEFAULTS.read_text(encoding="utf-8")
    pin_line = text.splitlines().index(f'llm_gpu_serving_nvidia_userspace_version: "{version}"')
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
    assert all("{{ llm_gpu_serving_nvidia_userspace_version }}" in name for name in names)
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
            llm_gpu_serving_nvidia_smi={"rc": rc, "stdout_lines": lines},
            llm_gpu_serving_nvidia_userspace_version=version,
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
    tasks = _load(ROLE_ROOT / "tasks/main.yml")
    names = [task["name"] for task in tasks]
    link = tasks[names.index("Put the vLLM and Hugging Face CLIs on the default PATH")]

    assert link["ansible.builtin.file"]["state"] == "link"
    assert {item["name"] for item in link["loop"]} == {"vllm", "hf"}
    assert names.index(link["name"]) > names.index("Install the pinned vLLM build with SM120 b12x kernels")
    assert names.index(link["name"]) > names.index("Install the pinned Hugging Face CLI in the model cache")
