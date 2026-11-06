"""Upstream age, deny-list, and controller policy-loading contracts."""

from __future__ import annotations

import json
import os
import subprocess
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jinja2
import pytest
import yaml

from test_model_trial import PLUGIN, ROOT, fixture, select


def test_original_model_uses_created_at_without_modification_date_fallback() -> None:
    data = fixture("nvidia-qwen38.json")
    data["info"]["cardData"] = {}
    data["info"]["createdAt"] = "2026-10-01T00:00:00Z"
    data["policy"]["models"][0]["upstream_id"] = data["info"]["id"]
    assert select(data)["release_date"] == "2026-10-01"
    data["info"]["createdAt"] = None
    with pytest.raises(ValueError, match="undated"):
        select(data)


@pytest.mark.parametrize("created", [None, "", "not-a-date"])
def test_undated_or_invalid_base_fails_closed(created) -> None:
    data = fixture("unsloth-qwen38-gguf.json")
    data["upstreams"]["example/base"]["createdAt"] = created
    with pytest.raises(ValueError):
        select(data)


def test_recent_quantization_inherits_old_base_and_is_rejected() -> None:
    data = fixture("unsloth-qwen38-gguf.json")
    data["upstreams"]["example/base"]["createdAt"] = "2025-01-01T00:00:00Z"
    # Even a recent policy entry cannot override the fetched upstream date.
    with pytest.raises(ValueError, match="old"):
        select(data)


@pytest.mark.parametrize("denied", ["example/quantized", "example/intermediate", "example/base"])
@pytest.mark.parametrize("record", [False, True])
def test_denied_model_or_ancestor_is_rejected(denied: str, record: bool) -> None:
    data = fixture("nvidia-qwen38.json")
    data["policy"]["deny"] = [{"id": denied, "reason": "fixture"} if record else denied]
    with pytest.raises(ValueError, match="denied"):
        select(data)


def test_missing_and_cyclic_ancestry_fail_closed() -> None:
    data = fixture("nvidia-qwen38.json")
    data["upstreams"].pop("example/base")
    with pytest.raises(ValueError, match="unresolved"):
        select(data)
    data = fixture("nvidia-qwen38.json")
    data["upstreams"]["example/base"]["cardData"] = {"base_model": "example/quantized"}
    data["upstreams"]["example/quantized"] = data["info"]
    with pytest.raises(ValueError, match="cyclic"):
        select(data)


@pytest.mark.parametrize("change", [
    {"upstream_created": None}, {"upstream_created": "2025-01-01"},
    {"upstream_id": "example/other"}, {"dossier": ""},
])
def test_trial_policy_requires_matching_dated_upstream_and_dossier(change: dict) -> None:
    data = fixture("nvidia-qwen38.json")
    data["policy"]["models"][0].update(change)
    with pytest.raises(ValueError):
        select(data)


@pytest.mark.parametrize("age,allowed", [(365, True), (366, False), (-1, False)])
def test_registry_policy_age_boundary(age: int, allowed: bool) -> None:
    policy = fixture("nvidia-qwen38.json")["policy"]
    policy["models"][0]["upstream_created"] = (
        datetime.now(timezone.utc).date() - timedelta(days=age)
    ).isoformat()
    model = {"client_model_id": "example/client", "upstream_model_id": "example/quantized"}
    assert PLUGIN._POLICY.registry_policy_allowed(model, policy) is allowed


@pytest.mark.parametrize("case", ["old", "undated", "denied", "unknown", "no-dossier"])
def test_registry_projection_excludes_ineligible_models(case: str) -> None:
    policy = fixture("nvidia-qwen38.json")["policy"]
    model = {"client_model_id": "example/client", "upstream_model_id": "example/quantized",
             "tier": "test", "enabled": True}
    if case == "old":
        model["lifecycle"] = "old"
    elif case == "undated":
        policy["models"][0]["upstream_created"] = None
    elif case == "denied":
        policy["deny"] = ["example/base"]
    elif case == "unknown":
        policy["models"] = []
    else:
        policy["models"][0]["dossier"] = ""
    defaults = yaml.safe_load((ROOT / "roles/llm_router/defaults/main/20-registry.yml").read_text())
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["llm_model_policy_allowed"] = PLUGIN._POLICY.registry_policy_allowed
    template = env.from_string(defaults["_llm_router_registry_enabled_source"])
    inputs = {"llm_router_model_registry": [model], "llm_model_trial_policy_data": policy}
    assert template.render(**inputs) == "[]"
    eligible = deepcopy(model)
    eligible.pop("lifecycle", None)
    inputs.update(llm_router_model_registry=[eligible],
                  llm_model_trial_policy_data=fixture("nvidia-qwen38.json")["policy"])
    assert "example/client" in template.render(**inputs)


@pytest.mark.parametrize("change", [
    {"version": 2}, {"max_age_days": 366}, {"max_age_days": True},
    {"as_of": None}, {"deny": [{}]},
])
def test_invalid_shared_policy_fails_closed(change: dict) -> None:
    data = fixture("nvidia-qwen38.json")
    data["policy"].update(change)
    with pytest.raises(ValueError):
        select(data)
    with pytest.raises(ValueError):
        PLUGIN._POLICY.registry_policy_allowed(
            {"client_model_id": "example/quantized", "upstream_model_id": "example/base"}, data["policy"]
        )


def test_registry_matches_only_the_actual_upstream_artifact() -> None:
    policy = fixture("nvidia-qwen38.json")["policy"]
    model = {"client_model_id": "example/quantized", "upstream_model_id": "example/unlisted"}
    assert not PLUGIN._POLICY.registry_policy_allowed(model, policy)
    model["upstream_model_id"] = "example/quantized"
    assert PLUGIN._POLICY.registry_policy_allowed(model, policy)
    policy["deny"] = [model["upstream_model_id"]]
    assert not PLUGIN._POLICY.registry_policy_allowed(model, policy)


@pytest.mark.parametrize("installed", [False, True])
def test_policy_loader_uses_installed_collection_before_role_input(tmp_path: Path, installed: bool) -> None:
    policy = fixture("nvidia-qwen38.json")["policy"]
    if installed:
        policy["deny"] = ["example/base"]
        path = tmp_path / "ansible_collections/dryvist/homelab/roles/llm_model_catalog/files/model-policy.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(policy))
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", "-c", "local",
         str(Path(__file__).with_name("test_model_policy.yml")), "-e",
         json.dumps({"expected_deny": policy["deny"], "expected_allowed": not installed})],
        cwd=ROOT, text=True, capture_output=True, timeout=60,
        env=os.environ | {"ANSIBLE_ROLES_PATH": str(ROOT / "roles"),
                          "ANSIBLE_COLLECTIONS_PATH": str(tmp_path)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
