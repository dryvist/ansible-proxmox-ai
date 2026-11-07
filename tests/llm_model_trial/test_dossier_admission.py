"""Admission consumes the shared dossier completion marker and upstream identity."""

from pathlib import Path

import pytest
import yaml

from test_model_trial import PLUGIN, fixture, select


@pytest.mark.parametrize("change,trial_reason,router_reason", [
    ({}, "", ""),
    ({"status": "started"}, "status must be complete", ""),
    ({"status": None}, "status must be complete", ""),
    ({"type": "Note"}, "type must be Model Dossier", "type must be Model Dossier"),
    ({"timestamp": "not-a-date"}, "parseable timestamp", "parseable timestamp"),
    ({"timestamp": None}, "parseable timestamp", "parseable timestamp"),
    ({"timestamp": "2026-02-30T00:00:00Z"}, "parseable timestamp", "parseable timestamp"),
])
def test_dossier_gate_is_complete_for_trials_only(
    tmp_path: Path, change: dict, trial_reason: str, router_reason: str
) -> None:
    data = fixture("nvidia-qwen38.json")
    policy = data["policy"]
    relative = Path(policy["models"][0]["dossier"]).relative_to("ansible")
    source = Path(policy["_dossier_root"]) / relative
    metadata = yaml.safe_load(source.read_text().split("---")[1]) | change
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text("---\n" + yaml.safe_dump(metadata) + "---\n")
    policy["_dossier_root"] = str(tmp_path)
    model = {"upstream_model_id": data["info"]["id"]}
    assert PLUGIN._POLICY.registry_policy_allowed(model, policy) is (not router_reason)
    assert router_reason in PLUGIN._POLICY.registry_policy_reason(model, policy)
    if trial_reason:
        with pytest.raises(ValueError, match=trial_reason):
            select(data)
    else:
        assert select(data)["upstream_id"] == "example/base"


def test_hosted_tier_is_outside_the_self_hosted_policy() -> None:
    policy = fixture("nvidia-qwen38.json")["policy"]
    hosted = {"tier": "opencode", "upstream_model_id": "provider/unlisted"}
    local = {"tier": "gpu", "upstream_model_id": "provider/unlisted"}
    assert PLUGIN._POLICY.registry_policy_allowed(hosted, policy, ["gpu"])
    assert not PLUGIN._POLICY.registry_policy_allowed(local, policy, ["gpu"])
    assert not PLUGIN._POLICY.registry_policy_allowed(hosted, policy)


def test_resolved_artifact_identity_wins_over_the_client_alias() -> None:
    data = fixture("nvidia-qwen38.json")
    artifact = data["policy"]["models"][0]["id"]
    aliased = {"upstream_model_id": "alias", "policy_artifact_id": artifact}
    assert PLUGIN._POLICY.registry_policy_allowed(aliased, data["policy"])
    assert not PLUGIN._POLICY.registry_policy_allowed({"upstream_model_id": "alias"}, data["policy"])


def test_replace_pending_admits_only_a_dated_denied_incumbent() -> None:
    policy = fixture("nvidia-qwen38.json")["policy"]
    policy["deny"] = [
        {"id": "example/old", "reason": "upstream_created 2025-01-02; age exceeds 365 days"},
        {"id": "example/undated", "reason": "undated"},
        {"id": "example/bad-date", "reason": "upstream_created 2025-13-45; age exceeds 365 days"},
        "example/no-reason",
    ]
    denied = {"policy_artifact_id": "example/old"}
    assert not PLUGIN._POLICY.registry_policy_allowed(denied, policy)
    assert PLUGIN._POLICY.registry_policy_allowed(denied | {"replace_pending": True}, policy)
    for artifact in ("example/unlisted", "example/undated", "example/bad-date", "example/no-reason"):
        marked = {"policy_artifact_id": artifact, "replace_pending": True}
        assert not PLUGIN._POLICY.registry_policy_allowed(marked, policy)


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
        {"upstream_model_id": data["info"]["id"]}, data["policy"]
    )


def test_unlisted_upstream_cannot_reuse_a_listed_client_name() -> None:
    data = fixture("nvidia-qwen38.json")
    model = {"client_model_id": data["policy"]["models"][0]["id"],
             "upstream_model_id": "example/unlisted"}
    assert not PLUGIN._POLICY.registry_policy_allowed(model, data["policy"])
    data["upstreams"]["example/base"]["id"] = "example/unlisted"
    data["upstreams"]["example/unlisted"] = data["upstreams"].pop("example/base")
    data["upstreams"]["example/intermediate"]["cardData"]["base_model"] = "example/unlisted"
    with pytest.raises(ValueError, match="does not match the Hub base_model chain"):
        select(data)


def test_sibling_evidence_does_not_override_the_requested_artifact() -> None:
    data = fixture("nvidia-qwen38.json")
    policy = data["policy"]
    policy["models"].append(policy["models"][0] | {"id": "example/another-quant"})
    assert select(data)["upstream_id"] == "example/base"
    policy["models"][-1]["dossier"] = "other.md"
    assert select(data)["upstream_id"] == "example/base"
    policy["models"].append(policy["models"][0])
    with pytest.raises(ValueError, match="conflicting"):
        select(data)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("record", [False, True])
def test_denied_quantization_does_not_reject_allowed_sibling(reverse: bool, record: bool) -> None:
    data = fixture("nvidia-qwen38.json")
    policy = data["policy"]
    denied = data["info"]["id"]
    sibling = "example/another-quant"
    policy["models"].append(policy["models"][0] | {"id": sibling})
    policy["deny"] = [{"id": denied, "reason": "fixture"} if record else denied]
    if reverse:
        policy["models"].reverse()
    for artifact, allowed in [(denied, False), (sibling, True)]:
        model = {"client_model_id": "example/client", "upstream_model_id": artifact}
        assert PLUGIN._POLICY.registry_policy_allowed(model, policy) is allowed
        data["info"]["id"] = artifact
        if allowed:
            assert select(data)["upstream_id"] == "example/base"
        else:
            with pytest.raises(ValueError, match="denied"):
                select(data)


def test_quantized_artifact_uses_its_listed_base_model_evidence() -> None:
    data = fixture("nvidia-qwen38.json")
    entry = data["policy"]["models"][0]
    assert entry["id"] == data["info"]["id"]
    assert entry["upstream_id"] != entry["id"]
    model = {"client_model_id": "example/client", "upstream_model_id": entry["id"]}
    assert PLUGIN._POLICY.registry_policy_allowed(model, data["policy"])
    assert select(data)["upstream_id"] == entry["upstream_id"]


@pytest.mark.parametrize("artifact", ["example/unlisted", "example/base"])
def test_unlisted_artifact_cannot_reuse_a_client_name_or_base(artifact: str) -> None:
    data = fixture("nvidia-qwen38.json")
    model = {"client_model_id": data["policy"]["models"][0]["id"],
             "upstream_model_id": artifact}
    assert not PLUGIN._POLICY.registry_policy_allowed(model, data["policy"])
    data["info"]["id"] = artifact
    data["info"]["cardData"] = {}
    with pytest.raises(ValueError, match="artifact_id has no shared policy record"):
        select(data)
