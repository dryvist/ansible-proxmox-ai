"""The medium vLLM campaign target is selected by lane."""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import yaml

from _group_vars import load_group_vars_all

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_medium_vllm_campaign_target_is_the_declared_medium_guest():
    """The campaign target group holds exactly the medium vLLM guest, never empty.

    Evaluates the loader's own flag expression and per-container add conditions
    against the medium guest's real host_vars and a container set shaped like
    tofu_data.containers, so a selector that drops the guest fails here.
    """
    loader = _load(REPO_ROOT / "inventory/load_tofu/add_lxc_hosts.yml")
    flag_task = next(
        task for task in loader
        if task.get("name") == "Record whether the started vLLM guest is the medium campaign target"
    )
    add_task = next(
        task for task in loader
        if task.get("name") == "Add the started vLLM guest to the medium campaign target group"
    )
    environment = jinja2.Environment()
    # Ansible adds `match` (re.match) and `bool` filters; plain Jinja has neither.
    environment.tests["match"] = lambda value, pattern: re.match(pattern, str(value)) is not None
    environment.filters["bool"] = lambda value: (
        value if isinstance(value, bool) else str(value).lower() in {"true", "yes", "on", "1", "y"}
    )
    guest = "llm-blackwell-7-96GB-medium"
    hostvars = {
        guest: _load(REPO_ROOT / "inventory/host_vars" / f"{guest}.yml"),
        "llm-blackwell-7-96GB-small": _load(REPO_ROOT / "inventory/host_vars/llm-blackwell-7-96GB-small.yml"),
    }
    containers = {
        guest: {"llm_gpu_engine_identity": "vllm", "started": True, "tags": ["llm-vllm", "nvidia-gpu"]},
        "llm-blackwell-7-96GB-small": {
            "llm_gpu_engine_identity": "llama_cpp",
            "started": True,
            "tags": ["llm-gpu-small", "nvidia-gpu"],
        },
    }

    flag_source = flag_task["ansible.builtin.set_fact"]["load_tofu_medium_vllm_campaign"]
    flag = environment.from_string(flag_source).render(
        load_tofu_gpu_engine_set_present=True,
        load_tofu_gpu_engine_selector="vllm",
        llm_gpu_active_profiles_by_engine=load_group_vars_all()["llm_gpu_active_profiles_by_engine"],
    ).strip() == "True"

    def selected(key: str) -> bool:
        context = {
            "item": {"key": key, "value": containers[key]},
            "hostvars": hostvars,
            "load_tofu_medium_vllm_campaign": flag,
        }
        return all(environment.compile_expression(condition)(context) for condition in add_task["when"])

    target = [key for key in containers if selected(key)]
    assert flag, "load_tofu_medium_vllm_campaign is False for the real inventory shape"
    assert target == [guest], f"llm_campaign_medium_vllm_target would hold {target}, expected [{guest!r}]"
