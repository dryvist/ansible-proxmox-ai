"""The active profile's declared serving capacity renders into textfile metrics."""

from __future__ import annotations

from pathlib import Path

import jinja2
import jinja2.nativetypes
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VLLM_ROOT = REPO_ROOT / "roles/vllm_serving"
LEGACY_ROOT = REPO_ROOT / "roles/llm_gpu_serving"
ROUTER_FAST_LANE_DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles/llm_router/defaults/main/10-network.yml").read_text(
        encoding="utf-8"
    )
)
ROUTER_GPU_DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles/llm_router/defaults/main/21-gpu-profiles.yml").read_text(
        encoding="utf-8"
    )
)
METRIC_FILE = "llm_serving_declared.prom"
LEGACY_GROUP_HOST = "legacy-gpu-fixture"
ENGINE_GROUP_HOST = "engine-gpu-fixture"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _copy_task(role_root: Path) -> dict:
    tasks = _load(role_root / "tasks/publish-declared-metrics.yml")
    return next(task for task in tasks if "ansible.builtin.copy" in task)


def _render_content(role_root: Path, context: dict) -> str:
    """Render the copy task's vars in declaration order, then its content."""
    # Native evaluation matches Ansible: a task var whose scalar is one full
    # expression resolves to the expression's native value (dict, int, str).
    env = jinja2.nativetypes.NativeEnvironment(
        undefined=jinja2.StrictUndefined, keep_trailing_newline=True
    )
    rendered = dict(context)
    for name, expression in _copy_task(role_root)["vars"].items():
        rendered[name] = env.from_string(str(expression)).render(**rendered)
    content_env = jinja2.Environment(
        undefined=jinja2.StrictUndefined, keep_trailing_newline=True
    )
    return content_env.from_string(
        _copy_task(role_root)["ansible.builtin.copy"]["content"]
    ).render(**rendered)


def _labels(accelerator: str, profile: str, engine: str) -> str:
    return f'{{accelerator="{accelerator}",profile="{profile}",engine="{engine}"}}'


def _vllm_context() -> dict:
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
        "_vllm_serving_router_fast_lane_defaults": ROUTER_FAST_LANE_DEFAULTS,
        "_vllm_serving_router_gpu_defaults": ROUTER_GPU_DEFAULTS,
    }


def _legacy_context(profile_name: str) -> dict:
    return {
        "llm_gpu_serving_profiles": _load(
            LEGACY_ROOT / "defaults/main/10-profiles.yml"
        )["llm_profiles"],
        "llm_active_profile": profile_name,
        "_llm_gpu_serving_router_fast_lane_defaults": ROUTER_FAST_LANE_DEFAULTS,
        "_llm_gpu_serving_router_gpu_defaults": ROUTER_GPU_DEFAULTS,
    }


def test_vllm_medium_profile_declares_its_slots_and_context():
    profiles_file = _load(VLLM_ROOT / "defaults/main/10-profiles.yml")
    profile = profiles_file["vllm_serving_profiles_core"]["medium-a"]
    context = _vllm_context() | {
        "inventory_hostname": ENGINE_GROUP_HOST,
        "groups": {"llm_gpu_legacy_group": [LEGACY_GROUP_HOST]},
    }
    labels = _labels(
        ROUTER_GPU_DEFAULTS["llm_router_gpu_accelerator"],
        "medium-a",
        profiles_file["vllm_serving_engine"],
    )
    assert _render_content(VLLM_ROOT, context) == (
        f"llm_serving_declared_slots{labels} {profile['max_num_seqs']}\n"
        f"llm_serving_declared_context_tokens_per_slot{labels} {profile['max_model_len']}\n"
    )


def test_legacy_group_members_label_the_vllm_lane_gpu_secondary():
    profiles_file = _load(VLLM_ROOT / "defaults/main/10-profiles.yml")
    context = _vllm_context() | {
        "inventory_hostname": LEGACY_GROUP_HOST,
        "groups": {"llm_gpu_legacy_group": [LEGACY_GROUP_HOST]},
    }
    labels = _labels(
        ROUTER_FAST_LANE_DEFAULTS["llm_router_llm_fast_accelerator"],
        "medium-a",
        profiles_file["vllm_serving_engine"],
    )
    assert f"llm_serving_declared_slots{labels} " in _render_content(VLLM_ROOT, context)


def test_small_llama_cpp_profile_declares_its_slots_and_per_slot_context():
    profile = _load(LEGACY_ROOT / "defaults/main/10-profiles.yml")["llm_profiles"][
        "small"
    ]
    context = _legacy_context("small") | {
        "inventory_hostname": ENGINE_GROUP_HOST,
        "groups": {"llm_gpu_legacy_group": [LEGACY_GROUP_HOST]},
    }
    labels = _labels(
        ROUTER_GPU_DEFAULTS["llm_router_gpu_accelerator"], "small", profile["engine"]
    )
    # llama-cpp-serve-args.j2 starts the server with --ctx-size
    # max_model_len x max_num_seqs and --parallel max_num_seqs, so the context
    # the server actually gets per slot is max_model_len.
    assert _render_content(LEGACY_ROOT, context) == (
        f"llm_serving_declared_slots{labels} {profile['max_num_seqs']}\n"
        f"llm_serving_declared_context_tokens_per_slot{labels} {profile['max_model_len']}\n"
    )


def test_declared_metrics_skip_without_the_collector_and_never_fail():
    for role_root, role_prefix, activation, defaults_file, render_only_flag in (
        (
            VLLM_ROOT,
            "vllm_serving",
            "tasks/activate.yml",
            "defaults/main/20-metrics.yml",
            "vllm_serving_render_only",
        ),
        (
            LEGACY_ROOT,
            "llm_gpu_serving",
            "tasks/main.yml",
            "defaults/main/00-core.yml",
            "llm_gpu_serving_render_only",
        ),
    ):
        tasks = _load(role_root / "tasks/publish-declared-metrics.yml")
        stat_task = next(task for task in tasks if "ansible.builtin.stat" in task)
        copy_task = _copy_task(role_root)
        include = next(
            task
            for task in _load(role_root / activation)
            if task.get("name")
            == "Publish the active profile's declared serving metrics"
        )
        assert stat_task["ansible.builtin.stat"]["path"] == (
            f"{{{{ {role_prefix}_textfile_collector_dir }}}}"
        )
        # The directory check is the only gate: without node_exporter the write
        # skips and nothing in this task file can fail the converge.
        assert copy_task["when"] == f"_{role_prefix}_textfile_collector_dir.stat.exists"
        assert copy_task["ansible.builtin.copy"]["mode"] == "0644"
        assert copy_task["ansible.builtin.copy"]["dest"] == (
            f"{{{{ {role_prefix}_textfile_collector_dir }}}}/{METRIC_FILE}"
        )
        assert f"{role_prefix}_textfile_collector_dir" in _load(
            role_root / defaults_file
        )
        assert render_only_flag in include["when"]
