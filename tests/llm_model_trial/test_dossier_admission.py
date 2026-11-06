"""Admission consumes the shared dossier completion marker and upstream identity."""

from pathlib import Path

import pytest
import yaml

from test_model_trial import PLUGIN, fixture, select


@pytest.mark.parametrize("change,reason", [
    ({}, ""),
    ({"status": "started"}, "status must be complete"),
    ({"status": None}, "status must be complete"),
    ({"type": "Note"}, "type must be Model Dossier"),
    ({"timestamp": "not-a-date"}, "parseable timestamp"),
    ({"timestamp": None}, "parseable timestamp"),
    ({"timestamp": "2026-02-30T00:00:00Z"}, "parseable timestamp"),
])
def test_dossier_completion_gate(tmp_path: Path, change: dict, reason: str) -> None:
    data = fixture("nvidia-qwen38.json")
    policy = data["policy"]
    relative = Path(policy["models"][0]["dossier"]).relative_to("ansible")
    source = Path(policy["_dossier_root"]) / relative
    metadata = yaml.safe_load(source.read_text().split("---")[1]) | change
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text("---\n" + yaml.safe_dump(metadata) + "---\n")
    policy["_dossier_root"] = str(tmp_path)
    model = {"upstream_model_id": "example/base"}
    assert PLUGIN._POLICY.registry_policy_allowed(model, policy) is (not reason)
    assert reason in PLUGIN._POLICY.registry_policy_reason(model, policy)
    if reason:
        with pytest.raises(ValueError, match=reason):
            select(data)
    else:
        assert select(data)["upstream_id"] == "example/base"


@pytest.mark.parametrize("dossier,reason", [
    ("ansible/roles/llm_model_catalog/files/dossiers/missing.md", "cannot be read"),
    ("nonempty", "repo-relative dossier path"),
    ({"as_of": "2026-11-06"}, "repo-relative dossier path"),
    ("ansible/roles/llm_model_catalog/files/dossiers/../../escape.md", "escapes"),
])
def test_invalid_dossier_paths_fail_closed(dossier, reason: str) -> None:
    data = fixture("nvidia-qwen38.json")
    data["policy"]["models"][0]["dossier"] = dossier
    with pytest.raises(ValueError, match=reason):
        select(data)
    assert not PLUGIN._POLICY.registry_policy_allowed(
        {"upstream_model_id": "example/base"}, data["policy"]
    )


def test_unlisted_upstream_cannot_reuse_a_listed_client_name() -> None:
    data = fixture("nvidia-qwen38.json")
    model = {"client_model_id": data["policy"]["models"][0]["id"],
             "upstream_model_id": "example/unlisted"}
    assert not PLUGIN._POLICY.registry_policy_allowed(model, data["policy"])
    data["upstreams"]["example/base"]["id"] = "example/unlisted"
    data["upstreams"]["example/unlisted"] = data["upstreams"].pop("example/base")
    data["upstreams"]["example/intermediate"]["cardData"]["base_model"] = "example/unlisted"
    with pytest.raises(ValueError, match="upstream_id has no shared policy record"):
        select(data)


def test_consistent_evidence_for_a_shared_upstream_is_admitted() -> None:
    data = fixture("nvidia-qwen38.json")
    policy = data["policy"]
    policy["models"].append(policy["models"][0] | {"id": "example/another-quant"})
    assert select(data)["upstream_id"] == "example/base"
    policy["models"][-1]["dossier"] = "other.md"
    with pytest.raises(ValueError, match="conflicting"):
        select(data)
