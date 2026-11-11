"""The active profile's declared serving capacity renders into textfile metrics."""

from __future__ import annotations

from pathlib import Path

import jinja2
import jinja2.nativetypes
import yaml

from _group_vars import load_group_vars_all

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / "roles/llm_serving_declared_metrics"
VLLM_ROOT = REPO_ROOT / "roles/vllm_serving"
LEGACY_ROOT = REPO_ROOT / "roles/llm_gpu_serving"
GROUP_VARS = REPO_ROOT / "inventory/group_vars"
PUBLISH_CALL = "Publish the active profile's declared serving metrics"
TEXTFILE_DIR_KEY = "llm_serving_declared_metrics_textfile_collector_dir"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


DEFAULTS = _load(SHARED_ROOT / "defaults/main/00-textfile.yml")
COLLECTOR_DIR = DEFAULTS[TEXTFILE_DIR_KEY]
METRIC_FILE = DEFAULTS["llm_serving_declared_metrics_metric_file"]
# A guest inherits the group_vars/all label; legacy-group members override it.
PRIMARY_LABEL = load_group_vars_all()["llm_gpu_accelerator"]
LEGACY_LABEL = _load(GROUP_VARS / "llm_gpu_legacy_group.yml")["llm_gpu_accelerator"]

# Start-command lines each caller's comment cites: template, then line -> flag.
CITED_TEMPLATE_LINES = (
    (VLLM_ROOT, "tasks/activate.yml", "templates/vllm-serving.service.j2",
     {30: "--max-model-len", 31: "--max-num-seqs"}),
    (LEGACY_ROOT, "tasks/main.yml", "templates/llm-gpu-serving.service.j2",
     {32: "--max-model-len", 33: "--max-num-seqs"}),
    (LEGACY_ROOT, "tasks/main.yml", "templates/llama-cpp-serve-args.j2",
     {14: "--ctx-size", 15: "--parallel"}),
    (LEGACY_ROOT, "tasks/main.yml", "templates/llm-gpu-serving-host.service.j2",
     {25: "--max-model-len", 26: "--max-num-seqs"}),
)


def _native(expression, context: dict):
    env = jinja2.nativetypes.NativeEnvironment(undefined=jinja2.StrictUndefined)
    return env.from_string(str(expression)).render(**context)


def _text(template: str, context: dict) -> str:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    return env.from_string(template).render(**context)


def _task(path: Path, name: str) -> dict:
    return next(task for task in _load(path) if task.get("name") == name)


def _module_task(path: Path, module: str) -> dict:
    return next(task for task in _load(path) if module in task)


def _render_publish(role_root: Path, activation: str, context: dict) -> tuple[str, str]:
    """Render the caller's include vars, then the shared copy task's dest and content."""
    call = _task(role_root / activation, PUBLISH_CALL)
    rendered = {**DEFAULTS, **context}
    for name, expression in call["vars"].items():
        rendered[name] = _native(expression, rendered)
    copy = _module_task(SHARED_ROOT / "tasks/publish.yml", "ansible.builtin.copy")
    for name, expression in copy.get("vars", {}).items():
        rendered[name] = _native(expression, rendered)
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


def _vllm_context(accelerator: str) -> dict:
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
        "llm_gpu_accelerator": accelerator,
    }


def _legacy_context(profile: str, accelerator: str) -> dict:
    return {
        "llm_gpu_serving_profiles": _load(LEGACY_ROOT / "defaults/main/10-profiles.yml")[
            "llm_profiles"
        ],
        "llm_active_profile": profile,
        "llm_gpu_accelerator": accelerator,
    }


def test_vllm_medium_profile_declares_its_slots_and_context():
    context = _vllm_context(PRIMARY_LABEL)
    profile = context["vllm_serving_profiles"]["medium-a"]
    dest, content = _render_publish(VLLM_ROOT, "tasks/activate.yml", context)
    assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
    assert content == _expected(
        PRIMARY_LABEL,
        "medium-a",
        context["vllm_serving_engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_legacy_group_member_labels_the_vllm_lane_gpu_secondary():
    assert LEGACY_LABEL != PRIMARY_LABEL
    context = _vllm_context(LEGACY_LABEL)
    profile = context["vllm_serving_profiles"]["medium-a"]
    _, content = _render_publish(VLLM_ROOT, "tasks/activate.yml", context)
    assert content == _expected(
        LEGACY_LABEL,
        "medium-a",
        context["vllm_serving_engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_legacy_llama_cpp_profile_declares_its_slots_and_per_slot_context():
    context = _legacy_context("small", LEGACY_LABEL)
    profile = context["llm_gpu_serving_profiles"]["small"]
    dest, content = _render_publish(LEGACY_ROOT, "tasks/main.yml", context)
    assert dest == f"{COLLECTOR_DIR}/{METRIC_FILE}"
    assert content == _expected(
        LEGACY_LABEL,
        "small",
        profile["engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_non_legacy_llama_cpp_profile_labels_gpu_primary():
    context = _legacy_context("small", PRIMARY_LABEL)
    profile = context["llm_gpu_serving_profiles"]["small"]
    _, content = _render_publish(LEGACY_ROOT, "tasks/main.yml", context)
    assert content == _expected(
        PRIMARY_LABEL,
        "small",
        profile["engine"],
        profile["max_num_seqs"],
        profile["max_model_len"],
    )


def test_cited_start_template_lines_carry_the_declared_flags():
    for root, caller, template, lines in CITED_TEMPLATE_LINES:
        template_lines = (root / template).read_text(encoding="utf-8").splitlines()
        for number, flag in lines.items():
            assert flag in template_lines[number - 1], (template, number)
        assert template.rsplit("/", 1)[1] in (root / caller).read_text(encoding="utf-8")


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
    for root, caller, render_only in (
        (VLLM_ROOT, "tasks/activate.yml", "vllm_serving_render_only"),
        (LEGACY_ROOT, "tasks/main.yml", "llm_gpu_serving_render_only"),
    ):
        call = _task(root / caller, PUBLISH_CALL)
        assert call["ansible.builtin.include_role"] == {
            "name": "llm_serving_declared_metrics",
            "tasks_from": "publish.yml",
        }
        assert render_only in call["when"]
        # The directory is declared once, in the shared role's defaults.
        for defaults_file in (root / "defaults/main").glob("*.yml"):
            assert TEXTFILE_DIR_KEY not in defaults_file.read_text(encoding="utf-8")
