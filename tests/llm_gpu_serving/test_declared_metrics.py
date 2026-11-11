"""The active profile's declared serving capacity renders into textfile metrics."""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import jinja2.nativetypes
import yaml

from _group_vars import group_var

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLES = REPO_ROOT / "roles"
SHARED_ROOT = ROLES / "llm_serving_declared_metrics"
VLLM_ROOT = ROLES / "vllm_serving"
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


def test_vllm_medium_profile_declares_its_slots_and_context():
    context = _vllm_context(legacy=False)
    profile = context["vllm_serving_profiles"]["medium-a"]
    dest, content = _render_publish(VLLM_ROOT, "tasks/activate.yml", context)
    assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
    assert content == _expected(
        group_var("llm_gpu_accelerator"),
        "medium-a",
        context["vllm_serving_engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_legacy_group_member_labels_the_vllm_lane_gpu_secondary():
    context = _vllm_context(legacy=True)
    profile = context["vllm_serving_profiles"]["medium-a"]
    _, content = _render_publish(VLLM_ROOT, "tasks/activate.yml", context)
    assert content == _expected(
        group_var("llm_gpu_accelerator", LEGACY_GROUP),
        "medium-a",
        context["vllm_serving_engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_legacy_llama_cpp_profile_declares_its_slots_and_per_slot_context():
    context = _legacy_context("small", legacy=True)
    profile = context["llm_gpu_serving_profiles"]["small"]
    dest, content = _render_publish(LEGACY_ROOT, "tasks/main.yml", context)
    assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
    assert content == _expected(
        group_var("llm_gpu_accelerator", LEGACY_GROUP),
        "small",
        profile["engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_active_and_co_resident_profiles_are_published_together():
    selected_profiles = ["small", "small-c4-64k"]
    context = _legacy_context(
        "small",
        legacy=True,
        co_resident_profiles=selected_profiles[1:],
    )
    dest, content = _render_publish(LEGACY_ROOT, "tasks/main.yml", context)
    accelerator = group_var("llm_gpu_accelerator", LEGACY_GROUP)
    expected = "".join(
        _expected(
            accelerator,
            profile_name,
            context["llm_gpu_serving_profiles"][profile_name]["engine"],
            context["llm_gpu_serving_profiles"][profile_name]["max_num_seqs"],
            context["llm_gpu_serving_profiles"][profile_name]["max_model_len"],
        )
        for profile_name in selected_profiles
    )
    assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
    assert content == expected


def test_non_legacy_llama_cpp_profile_labels_gpu_primary():
    context = _legacy_context("small", legacy=False)
    profile = context["llm_gpu_serving_profiles"]["small"]
    _, content = _render_publish(LEGACY_ROOT, "tasks/main.yml", context)
    assert content == _expected(
        group_var("llm_gpu_accelerator"),
        "small",
        profile["engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_llamacpp_serving_profile_publishes_through_the_shared_task():
    context = _llamacpp_context("small", legacy=False)
    profile = context["llamacpp_serving_profiles"]["small"]
    dest, content = _render_publish(LLAMACPP_ROOT, "tasks/activate.yml", context)
    assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
    assert content == _expected(
        group_var("llm_gpu_accelerator"),
        "small",
        context["llamacpp_serving_engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_declared_metrics_match_rendered_start_flag_values():
    vllm_context = _vllm_context(legacy=False)
    vllm_profile = vllm_context["vllm_serving_profiles"]["medium-a"]
    _, vllm_content = _render_publish(VLLM_ROOT, "tasks/activate.yml", vllm_context)
    assert vllm_content == _expected(
        group_var("llm_gpu_accelerator"),
        "medium-a",
        vllm_context["vllm_serving_engine"],
        *_capacity_from_flags(
            VLLM_ROOT / "templates/vllm-serving.service.j2",
            {"vllm_serving_profile": vllm_profile},
            "vllm",
        ),
    )

    llamacpp_context = _llamacpp_context("small", legacy=False)
    llamacpp_profile = llamacpp_context["llamacpp_serving_profiles"]["small"]
    _, llamacpp_content = _render_publish(
        LLAMACPP_ROOT,
        "tasks/activate.yml",
        llamacpp_context,
    )
    assert llamacpp_content == _expected(
        group_var("llm_gpu_accelerator"),
        "small",
        llamacpp_context["llamacpp_serving_engine"],
        *_capacity_from_flags(
            LLAMACPP_ROOT / "templates/llama-cpp-serve-args.j2",
            {"lc_profile": llamacpp_profile},
            "llama_cpp",
        ),
    )

    legacy_context = _legacy_context(
        "small",
        legacy=True,
        co_resident_profiles=["small-c4-64k"],
    )
    selected_profiles = ["small", "small-c4-64k"]
    _, legacy_content = _render_publish(LEGACY_ROOT, "tasks/main.yml", legacy_context)
    legacy_accelerator = group_var("llm_gpu_accelerator", LEGACY_GROUP)
    legacy_expected = "".join(
        _expected(
            legacy_accelerator,
            profile_name,
            legacy_context["llm_gpu_serving_profiles"][profile_name]["engine"],
            *_capacity_from_flags(
                LEGACY_ROOT / "templates/llama-cpp-serve-args.j2",
                {"lc_profile": legacy_context["llm_gpu_serving_profiles"][profile_name]},
                "llama_cpp",
            ),
        )
        for profile_name in selected_profiles
    )
    assert legacy_content == legacy_expected

    gpu_vllm_context = _legacy_context("medium-a", legacy=True)
    gpu_vllm_profile = gpu_vllm_context["llm_gpu_serving_profiles"]["medium-a"]
    _, gpu_vllm_content = _render_publish(LEGACY_ROOT, "tasks/main.yml", gpu_vllm_context)
    gpu_vllm_expected = _expected(
        legacy_accelerator,
        "medium-a",
        gpu_vllm_profile["engine"],
        *_capacity_from_flags(
            LEGACY_ROOT / "templates/llm-gpu-serving.service.j2",
            {"llm_gpu_serving_profile": gpu_vllm_profile},
            "vllm",
        ),
    )
    assert gpu_vllm_content == gpu_vllm_expected
    assert gpu_vllm_content == _expected(
        legacy_accelerator,
        "medium-a",
        gpu_vllm_profile["engine"],
        *_capacity_from_flags(
            LEGACY_ROOT / "templates/llm-gpu-serving-host.service.j2",
            {"llm_gpu_serving_host_profile": gpu_vllm_profile},
            "vllm",
        ),
    )


def test_publish_skips_without_the_collector_and_never_fails():
    shared = _load(SHARED_ROOT / "tasks/publish.yml")
    stat = next(task for task in shared if "ansible.builtin.stat" in task)
    copy = next(task for task in shared if "ansible.builtin.copy" in task)
    assert stat["ansible.builtin.stat"]["path"] == "{{ llm_serving_declared_metrics_textfile_collector_dir }}"
    # The directory check is the only gate: without node_exporter the write
    # skips and nothing in this task file can fail the converge.
    assert copy["when"] == "_llm_serving_declared_metrics_collector.stat.exists"
    assert copy["ansible.builtin.copy"]["mode"] == "0644"
    assert copy["ansible.builtin.copy"]["dest"] == (
        "{{ llm_serving_declared_metrics_textfile_collector_dir }}/{{ llm_serving_declared_metrics_metric_file }}"
    )
    for root, activation, render_only in (
        (VLLM_ROOT, "tasks/activate.yml", "vllm_serving_render_only"),
        (LEGACY_ROOT, "tasks/main.yml", "llm_gpu_serving_render_only"),
        (LLAMACPP_ROOT, "tasks/activate.yml", "llamacpp_serving_render_only"),
    ):
        call_name = LEGACY_PUBLISH_CALL if root == LEGACY_ROOT else PUBLISH_CALL
        call = _task(root / activation, call_name)
        assert call["ansible.builtin.include_role"] == {
            "name": "llm_serving_declared_metrics",
            "tasks_from": "publish.yml",
        }
        # Each caller passes the profile selection, profile map and engine; the shared task derives the rest.
        expected_parameters = (
            (CALL_PARAMETERS - {"llm_serving_declared_metrics_profile"})
            | {"llm_serving_declared_metrics_profile_names"}
            if root == LEGACY_ROOT
            else CALL_PARAMETERS
        )
        assert set(call["vars"]) == expected_parameters
        assert render_only in call["when"]
    # The directory is declared once, in the shared role's defaults.
    for root in (VLLM_ROOT, LEGACY_ROOT, LLAMACPP_ROOT):
        for defaults_file in (root / "defaults/main").glob("*.yml"):
            assert TEXTFILE_DIR_KEY not in defaults_file.read_text(encoding="utf-8")
