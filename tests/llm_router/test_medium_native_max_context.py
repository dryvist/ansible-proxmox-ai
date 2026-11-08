"""One medium deployment admits the medium model's full native context.

The native maximum is read from the model-card facts the artifact registry
records (context_window_tokens with model_task_source: model_card), never
from this file. A long-context caller must always have a medium leg that
admits the full native window, and the pre-call context checks can only
steer an oversize prompt to that leg when it is the widest medium member,
so both properties are asserted here.
"""

from pathlib import Path

import yaml

from test_gpu_registry import _serving_profiles

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_DIR = REPO_ROOT / "llm-models.d"
ROUTER_CONFIG_TEMPLATE = REPO_ROOT / "roles/llm_router/templates/config.yaml.j2"
MEDIUM = "medium"
ENGINE_WINDOW_FIELD = "max_model_len"


def _slices():
    for path in sorted(REGISTRY_DIR.glob("*.yml")):
        for key, entries in yaml.safe_load(path.read_text(encoding="utf-8")).items():
            if key.startswith("_llm_model_artifacts") or key.endswith("_artifacts"):
                continue
            yield from (e for e in entries if isinstance(e, dict) and "client_model_id" in e)


def _artifacts_by_id():
    artifacts = {}
    for path in sorted(REGISTRY_DIR.glob("*artifacts*.yml")):
        for entries in yaml.safe_load(path.read_text(encoding="utf-8")).values():
            for artifact in entries:
                artifacts[artifact["artifact_id"]] = artifact
    return artifacts


def _medium_entries():
    return [entry for entry in _slices() if entry.get("route_group") == MEDIUM]


def _model_card_window(entry, artifacts):
    """The model-card context of the artifact serving this entry, or None."""
    artifact = artifacts.get(entry.get("artifact_id", ""))
    if artifact is None:
        same_repo = [
            candidate
            for candidate in artifacts.values()
            if candidate["hf_repo"] == entry["client_model_id"]
        ]
        artifact = same_repo[0] if len(same_repo) == 1 else None
    if artifact is None or artifact.get("model_task_source") != "model_card":
        return None
    return artifact.get("context_window_tokens")


def test_one_medium_deployment_admits_the_full_native_context():
    entries = _medium_entries()
    artifacts = _artifacts_by_id()
    native = {
        window
        for window in (_model_card_window(entry, artifacts) for entry in entries)
        if window is not None
    }
    assert len(native) == 1, f"medium artifacts disagree on the model card: {native}"
    native = native.pop()

    profiles = _serving_profiles()
    full_native = [
        entry
        for entry in entries
        if "context_window" in entry
        and entry["context_window"] + entry["max_output_tokens"] == native
        and profiles[entry["profile"]][ENGINE_WINDOW_FIELD] == native
    ]
    assert len(full_native) == 1, (
        f"expected exactly one medium deployment at the native maximum, "
        f"found {len(full_native)}"
    )
    winner = full_native[0]
    assert winner["profile"] == "medium-a"
    assert artifacts[winner["artifact_id"]]["context_window_tokens"] == native


def test_pre_call_checks_can_steer_oversize_prompts_to_that_leg():
    assert "enable_pre_call_checks: true" in ROUTER_CONFIG_TEMPLATE.read_text(encoding="utf-8")
    entries = [entry for entry in _medium_entries() if "context_window" in entry]
    winner = next(entry for entry in entries if entry["profile"] == "medium-a")
    others = [entry for entry in entries if entry is not winner]
    assert others, "the medium capability has no second member to compare against"
    assert all(winner["context_window"] > entry["context_window"] for entry in others)
