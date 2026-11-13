"""The active profile's declared serving capacity renders into textfile metrics."""

from __future__ import annotations

import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

import jinja2
import jinja2.nativetypes
import pytest
import yaml

from _group_vars import group_var

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLES = REPO_ROOT / "roles"
SHARED_ROOT = ROLES / "llm_serving_declared_metrics"
VLLM_ROOT = ROLES / "vllm_serving"
SGLANG_ROOT = ROLES / "sglang_serving"
LEGACY_ROOT = ROLES / "llm_gpu_serving"
LLAMACPP_ROOT = ROLES / "llamacpp_serving"
PUBLISH_CALL = "Publish the active profile's declared serving metrics"
LEGACY_PUBLISH_CALL = "Publish every selected GPU profile's declared serving metrics"
LEGACY_GROUP = "llm_gpu_legacy_group"
TEXTFILE_DIR_KEY = "llm_serving_declared_metrics_textfile_collector_dir"
CALL_PARAMETERS = {
    "llm_serving_declared_metrics_profile",
    "llm_serving_declared_metrics_profiles",
    "llm_serving_declared_metrics_engine",
}


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


DEFAULTS = _load(SHARED_ROOT / "defaults/main/00-textfile.yml")
COLLECTOR_DIR = DEFAULTS[TEXTFILE_DIR_KEY]
METRIC_FILE = DEFAULTS["llm_serving_declared_metrics_metric_file"]

def _guest_vars(legacy: bool) -> dict:
    """The guest's label as its inventory declares it: group_vars/all, or the legacy group."""
    return {"llm_gpu_accelerator": group_var("llm_gpu_accelerator", LEGACY_GROUP if legacy else None)}


def _native(expression, context: dict):
    env = jinja2.nativetypes.NativeEnvironment(undefined=jinja2.StrictUndefined)
    return env.from_string(str(expression)).render(**context)


def _text(template: str, context: dict) -> str:
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        lstrip_blocks=True,
        trim_blocks=True,
    )
    return env.from_string(template).render(**context)


def _task(path: Path, name: str) -> dict:
    return next(task for task in _load(path) if task.get("name") == name)


def _run_publish_task(tmp_path: Path, collector_dir: Path) -> subprocess.CompletedProcess[str]:
    ansible_playbook = shutil.which("ansible-playbook")
    if ansible_playbook is None:
        sibling = Path(sys.executable).with_name("ansible-playbook")
        ansible_playbook = str(sibling) if sibling.is_file() else None
    if ansible_playbook is None:
        pytest.skip("ansible-playbook is not available")

    playbook = tmp_path / "publish.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [
                        {"ansible.builtin.import_tasks": str(SHARED_ROOT / "tasks/publish.yml")}
                    ],
                    "vars": {
                        TEXTFILE_DIR_KEY: str(collector_dir),
                        "llm_serving_declared_metrics_metric_file": METRIC_FILE,
                        "llm_serving_declared_metrics_profile": "example",
                        "llm_serving_declared_metrics_profiles": {
                            "example": {
                                "engine": "example",
                                "max_num_seqs": 3,
                                "max_model_len": 4096,
                            }
                        },
                        "llm_serving_declared_metrics_engine": "example",
                        "llm_gpu_accelerator": "example",
                    },
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    local_tmp = tmp_path / "ansible-local"
    local_tmp.mkdir()
    env = {
        **os.environ,
        "ANSIBLE_LOCAL_TEMP": str(local_tmp),
        "ANSIBLE_NOCOLOR": "1",
        "TMPDIR": str(temp_dir),
    }
    return subprocess.run(
        [ansible_playbook, "-i", "localhost,", "-c", "local", str(playbook)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _module_task(path: Path, module: str) -> dict:
    return next(task for task in _load(path) if module in task)


def _render_publish(role_root: Path, activation: str, context: dict) -> tuple[str, str]:
    """Render the caller's include vars, then the shared copy task's dest and content."""
    call_name = LEGACY_PUBLISH_CALL if role_root == LEGACY_ROOT else PUBLISH_CALL
    call = _task(role_root / activation, call_name)
    rendered = {**DEFAULTS, **context}
    for name, expression in call["vars"].items():
        rendered[name] = _native(expression, rendered)
    copy = _module_task(SHARED_ROOT / "tasks/publish.yml", "ansible.builtin.copy")
    return (
        _text(copy["ansible.builtin.copy"]["dest"], rendered),
        _text(copy["ansible.builtin.copy"]["content"], rendered),
    )


def _expected(accelerator: str, profile: str, engine: str, slots, context) -> str:
    labels = f'{{accelerator="{accelerator}",profile="{profile}",engine="{engine}"}}'
    return (
        f"llm_serving_declared_slots{labels} {slots}\n"
        f"llm_serving_declared_context_tokens_per_slot{labels} {context}\n"
    )


def _vllm_context(legacy: bool) -> dict:
    profiles_file = _load(VLLM_ROOT / "defaults/main/10-profiles.yml")
    return {
        "vllm_serving_profiles": {
            **profiles_file["vllm_serving_profiles_core"],
            **_load(VLLM_ROOT / "defaults/main/11-profile-families.yml")[
                "vllm_serving_profiles_specialized"
            ],
        },
        "vllm_serving_active_profile": "medium-a",
        "vllm_serving_engine": profiles_file["vllm_serving_engine"],
    } | _guest_vars(legacy)


def _legacy_context(
    profile: str,
    legacy: bool,
    co_resident_profiles: list[str] | None = None,
) -> dict:
    return {
        "llm_gpu_serving_profiles": _load(LEGACY_ROOT / "defaults/main/10-profiles.yml")[
            "llm_profiles"
        ],
        "llm_active_profile": profile,
        "llm_gpu_serving_co_resident_profiles": co_resident_profiles or [],
    } | _guest_vars(legacy)


def _llamacpp_context(profile: str, legacy: bool) -> dict:
    profiles_file = _load(LLAMACPP_ROOT / "defaults/main/10-profiles.yml")
    return {
        "llamacpp_serving_profiles": profiles_file["llamacpp_serving_profiles"],
        "llamacpp_serving_active_profile": profile,
        "llamacpp_serving_engine": profiles_file["llamacpp_serving_engine"],
    } | _guest_vars(legacy)


def _rendered_flag(template: Path, context: dict, flag: str) -> int:
    lines = [
        line
        for line in template.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith(flag)
    ]
    assert len(lines) == 1, (template, flag, lines)
    rendered = _text(lines[0], context)
    match = re.search(rf"{re.escape(flag)}\s+(\d+)", rendered)
    assert match, (template, flag, rendered)
    return int(match.group(1))


def _capacity_from_flags(template: Path, context: dict, engine: str) -> tuple[int, int]:
    if engine == "llama_cpp":
        slots = _rendered_flag(template, context, "--parallel")
        shared_context = _rendered_flag(template, context, "--ctx-size")
        assert shared_context % slots == 0
        return slots, shared_context // slots

    slots = _rendered_flag(template, context, "--max-num-seqs")
    per_slot_context = _rendered_flag(template, context, "--max-model-len")
    return slots, per_slot_context
