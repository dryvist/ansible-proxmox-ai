"""Check the generated alias contract projects catalog-backed model IDs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_bolt_profiles_project_upstream_id_from_artifact_catalog():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_servable_aliases.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    contract = json.loads(result.stdout)
    bolt_models = [
        model for model in contract["models"] if model["client_model_id"].startswith("gpu-sweep-nemotron-bolt-")
    ]
    artifacts = yaml.safe_load((ROOT / "llm-models.d/69-gpu-artifacts-matrix.yml").read_text())
    bolt_artifact = next(
        artifact
        for artifact in artifacts["_llm_model_artifacts_matrix"]
        if artifact["artifact_id"] == "nemotron-bolt-24gb-gguf"
    )

    assert len(bolt_models) == 2
    assert {model["upstream_model_id"] for model in bolt_models} == {bolt_artifact["hf_repo"]}


def test_router_names_list_served_names_by_category():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_servable_aliases.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    router_names = json.loads(result.stdout)["router_names"]

    # Expected names come from the registry, never a re-typed literal.
    entries = [
        entry
        for path in sorted((ROOT / "llm-models.d").glob("*.yml"))
        for doc in [yaml.safe_load(path.read_text()) or {}]
        for value in doc.values()
        if isinstance(value, list)
        for entry in value
        if isinstance(entry, dict) and entry.get("client_model_id") and entry.get("enabled")
    ]
    served = next(e for e in entries if e.get("servable") and e.get("stable_aliases"))
    assert served["stable_aliases"][0] in router_names["aliases"]
    assert (entries[0].get("route_group") or entries[0]["client_model_id"]) in router_names["route_groups"]
    assert "judge" in router_names["roles"]
    for names in router_names.values():
        assert names == sorted(set(names))
    assert router_names["all"] == sorted(
        set().union(
            *(
                set(router_names[key])
                for key in ("aliases", "route_groups", "direct_routes", "roles")
            )
        )
    )
