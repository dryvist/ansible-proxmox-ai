"""Contract for the pinned Strata GPU serving role."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/strata_serving"
PLAYBOOK = REPO_ROOT / "playbooks/llm-serving-gpu-engines.yml"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_role_pins_the_trusted_artifact_and_supported_batch_sizes():
    defaults = _load(ROLE_ROOT / "defaults/main/00-engine.yml")

    assert defaults["strata_serving_version"] == "296b59d2f73c8f04a805df29c2fa97655adace78"
    assert defaults["strata_serving_artifact_id"] == "flash-next-iq3-s"
    assert defaults["strata_serving_trusted_repo"] == (
        "{{ nvidia_gpu_guest_artifacts_by_id[strata_serving_artifact_id].hf_repo }}"
    )
    assert defaults["strata_serving_context_tokens"] == 65536
    assert defaults["strata_serving_parallel"] in [1, 4, 8]
    assert defaults["strata_serving_request_lines"] == 1


def test_role_uses_official_setup_and_requires_the_generated_mtp_config():
    tasks = _load(ROLE_ROOT / "tasks/main.yml")
    serialized = yaml.safe_dump(tasks)

    assert "./setup.sh" in serialized
    assert "--setup" in serialized
    assert "--gguf-dir" in serialized
    assert "--parallel" in serialized
    assert "--no-start" in serialized
    assert "strata_serving_config_final.parallel" in serialized
    assert "--mtp" in serialized
    assert "strata_serving_config_final.args" in serialized
    assert "STRATA_REQUEST_LINES" in serialized
    assert "/metrics?format=prometheus" in serialized
    assert "api_key" not in serialized.lower()


def test_systemd_serves_the_generated_openai_compatible_runner_without_secrets():
    unit = (ROLE_ROOT / "templates/strata-serving.service.j2").read_text(encoding="utf-8")

    assert "ExecStart={{ strata_serving_run_script }}" in unit
    assert "Environment=STRATA_REQUEST_LINES={{ strata_serving_request_lines }}" in unit
    assert "EnvironmentFile" not in unit
    assert "api_key" not in unit.lower()


def test_selected_strata_play_targets_one_gpu_guest_and_runs_before_router():
    plays = _load(PLAYBOOK)
    strata = next(play for play in plays if play.get("hosts") == "llm_gpu_serving_strata_group")

    assert strata["serial"] == 1
    assert strata["any_errors_fatal"] is True
    assert any(
        task.get("name") == "Require the Strata GPU serving host to be in nvidia_gpu_group"
        for task in strata["pre_tasks"]
    )
    assert any(
        task.get("ansible.builtin.include_role", {}).get("name") == "strata_serving"
        for task in _walk(strata["tasks"])
    )


def test_campaign_uses_the_served_model_id_and_preserves_the_model_store_shards():
    target = _load(REPO_ROOT / "playbooks/llm-model-campaign-target.yml")[0]
    served_id = next(
        task for task in target["pre_tasks"]
        if task.get("name") == "Record the model ID exposed by the live endpoint"
    )
    assert served_id["ansible.builtin.set_fact"]["_llm_campaign_served_model_id"] == "{{ _llm_campaign_models_response.json.data[0].id }}"

    cache_sync = next(task for task in target["tasks"] if task.get("name") == "Download only the selected registry artifact into the target cache")
    assert cache_sync["when"] == "engine not in ['mlx_lm', 'strata']"
    gguf_find = next(task for task in target["pre_tasks"] if task.get("name") == "Resolve a single GGUF file when the selected artifact is GGUF")
    assert gguf_find["when"] == "engine == 'llama_cpp' and _llm_campaign_model.format == 'GGUF'"


def _walk(tasks):
    for task in tasks:
        yield task
        for child_key in ("block", "rescue", "always"):
            yield from _walk(task.get(child_key, []))
