"""The router's per-profile accelerator labels come from each GPU guest's own variable."""

from __future__ import annotations

from pathlib import Path

import jinja2
import jinja2.nativetypes
import yaml

from _group_vars import group_var

REPO_ROOT = Path(__file__).resolve().parents[2]
GPU_PROFILES = REPO_ROOT / "roles/llm_router/defaults/main/21-gpu-profiles.yml"
PRIMARY_LABEL = group_var("llm_gpu_accelerator")
LEGACY_LABEL = group_var("llm_gpu_accelerator", group="llm_gpu_legacy_group")
MEMBERS = [
    {"host": "primary-gpu-fixture", "active": "medium-a", "co_resident": ["small-c4-64k"]},
    {"host": "legacy-gpu-fixture", "active": "small", "co_resident": []},
]


def _mapping(members: list[dict], hostvars: dict, groups: dict | None = None) -> dict:
    template = yaml.safe_load(GPU_PROFILES.read_text(encoding="utf-8"))[
        "llm_router_gpu_profile_accelerators"
    ]
    env = jinja2.nativetypes.NativeEnvironment(undefined=jinja2.StrictUndefined)
    return env.from_string(template).render(
        llm_router_gpu_members=members, hostvars=hostvars, groups=groups or {}
    )


def test_each_profile_takes_its_members_guest_variable():
    hostvars = {
        "primary-gpu-fixture": {"llm_gpu_accelerator": PRIMARY_LABEL},
        "legacy-gpu-fixture": {"llm_gpu_accelerator": LEGACY_LABEL},
    }
    assert _mapping(MEMBERS, hostvars) == {
        "medium-a": PRIMARY_LABEL,
        "small-c4-64k": PRIMARY_LABEL,
        "small": LEGACY_LABEL,
    }


def test_group_membership_does_not_change_the_label():
    hostvars = {"primary-gpu-fixture": {"llm_gpu_accelerator": PRIMARY_LABEL}}
    groups = {"llm_gpu_legacy_group": ["primary-gpu-fixture"]}
    assert _mapping(MEMBERS[:1], hostvars, groups) == {
        "medium-a": PRIMARY_LABEL,
        "small-c4-64k": PRIMARY_LABEL,
    }


def test_a_declared_value_passes_through_unchanged():
    hostvars = {"primary-gpu-fixture": {"llm_gpu_accelerator": "gpu-fixture-lane"}}
    assert _mapping(MEMBERS[:1], hostvars) == {
        "medium-a": "gpu-fixture-lane",
        "small-c4-64k": "gpu-fixture-lane",
    }
