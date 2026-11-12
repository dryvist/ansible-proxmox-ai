"""The container-trial opt-in maps only tagged guests and refuses the live medium guest."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[2]
GROUP_TEMPLATE = ROOT / "inventory/load_tofu/add_lxc_host_groups.yml.j2"
GUARD = ROOT / "inventory/load_tofu/refuse_container_trial_live_guest.yml"
TRIAL_TAG = "llm-container-trial"
TRIAL_GROUP = "llm_gpu_container_trial_group"
LIVE_MEDIUM = "llm-blackwell-7-96GB-medium"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _environment() -> jinja2.Environment:
    environment = jinja2.Environment()
    environment.filters["bool"] = bool
    environment.filters["intersect"] = lambda left, right: [item for item in left if item in set(right)]
    environment.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
    return environment


def _groups_for(tags: list[str]) -> list[str]:
    rendered = _environment().from_string(GROUP_TEMPLATE.read_text(encoding="utf-8")).render(
        item={"key": "guest", "value": {"tags": tags}},
        _ai_agent_by_container={},
        load_tofu_gpu_engine_set_present=False,
        load_tofu_gpu_engine_selector="llama_cpp",
    )
    return ast.literal_eval(rendered)


def _guard_passes(key: str, value: dict) -> bool:
    assertion = next(task for task in _load(GUARD) if "ansible.builtin.assert" in task)
    refused = assertion["vars"]["_container_trial_refused_guests"]
    environment = _environment()
    return all(
        environment.compile_expression(expression)(
            item={"key": key, "value": value},
            _container_trial_refused_guests=refused,
        )
        for expression in assertion["ansible.builtin.assert"]["that"]
    )


def test_tagged_guest_joins_the_trial_group_and_untagged_guest_does_not():
    assert TRIAL_GROUP in _groups_for(["nvidia-gpu", "llm-gpu", TRIAL_TAG])
    assert TRIAL_GROUP not in _groups_for(["nvidia-gpu", "llm-gpu"])


def test_guard_refuses_the_live_medium_guest_carrying_the_trial_tag():
    assert not _guard_passes(LIVE_MEDIUM, {"hostname": LIVE_MEDIUM, "tags": [TRIAL_TAG]})


def test_guard_refuses_the_medium_hostname_under_any_inventory_key():
    assert not _guard_passes("alias", {"hostname": LIVE_MEDIUM, "tags": [TRIAL_TAG]})


def test_guard_allows_the_intended_small_guest_and_untagged_medium_guest():
    small = "llm-blackwell-7-96GB-small"
    assert _guard_passes(small, {"hostname": small, "tags": [TRIAL_TAG]})
    assert _guard_passes(LIVE_MEDIUM, {"hostname": LIVE_MEDIUM, "tags": ["nvidia-gpu"]})


def test_guard_is_loaded_before_any_host_is_added():
    tasks = _load(ROOT / "inventory/load_tofu/add_lxc_hosts.yml")
    imports = [task.get("ansible.builtin.import_tasks", "") for task in tasks]
    guard_index = imports.index("refuse_container_trial_live_guest.yml")
    assert guard_index < imports.index("add_lxc_host_inventory.yml")
