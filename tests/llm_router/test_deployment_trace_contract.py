"""Selected deployments supply trace identity before native exporters run."""

import asyncio
import json

import pytest

from test_request_metrics import module  # noqa: F401 - shared callback fixture


CARRIERS = ("requester_metadata", "spend_logs_metadata", "trace_metadata")


@pytest.mark.parametrize("slot", ["metadata", "litellm_metadata"])
@pytest.mark.parametrize("privacy", ["private", "public"])
def test_key_privacy_and_agent_reach_all_carriers(module, slot, privacy):
    data = {slot: {"agent": "fixture-agent", "tier": 0, "privacy_class": "spoofed"}}
    for carrier in CARRIERS:
        data[slot][carrier] = {"privacy_class": "spoofed", "model": "spoofed", "accelerator": "spoofed"}
    module["apply_trace_contract"](data, {"privacy_class": privacy, "trace_defaults": {
        "client": "fixture-client", "runner": "runner", "purpose": "live",
        "tier": 1,
    }}, "fixture-key")
    for carrier in CARRIERS:
        contract = data[slot][carrier]
        assert contract["agent"] == "fixture-agent"
        assert contract["tier"] == 0
        assert contract["privacy_class"] == privacy
        assert contract["model"] is None
        assert contract["accelerator"] is None


@pytest.mark.parametrize("privacy", [None, "", "private", "unknown"])
def test_missing_or_invalid_key_privacy_fails_closed(module, privacy):
    data = {"metadata": {"privacy_class": "public"}}
    key = {"privacy_class": privacy, "trace_defaults": {
        "client": "fixture-client", "runner": "runner", "purpose": "live", "privacy_class": "public",
    }}
    module["apply_trace_contract"](data, key, "fixture-key")
    for carrier in CARRIERS:
        assert data["metadata"][carrier]["privacy_class"] == "private"
    assert data["metadata"]["requester_metadata"]["agent"] == "fixture-key"


@pytest.mark.parametrize("slot", ["metadata", "litellm_metadata"])
@pytest.mark.parametrize("privacy", ["private", "public"])
def test_success_failure_retry_and_fallback_use_attempt_deployment(module, capsys, slot, privacy):
    data = {slot: {"agent": "fixture-agent", "tier": 0, "privacy_class": "spoofed"}}
    module["apply_trace_contract"](data, {"privacy_class": privacy}, "fixture-key")
    callback = module["RequestMetrics"]()
    attempts = [
        ("success", "fixture/primary", "gpu-primary"),
        ("failure", "fixture/primary", "gpu-primary"),
        ("success", "fixture/primary", "gpu-primary"),
        ("failure", "fixture/primary", "gpu-primary"),
        ("success", "fixture/fallback", "mac-primary"),
    ]
    for status, model, accelerator in attempts:
        # Router._update_kwargs_with_deployment replaces these on every attempt.
        metadata = {**data[slot], "deployment": model, "model_info": {"accelerator": accelerator}}
        kwargs = {"litellm_params": {slot: metadata}, "standard_logging_object": {
            "model": "requested-alias", "status": status, "completion_tokens": 7 if status == "success" else 0,
            "metadata": {carrier: dict(metadata[carrier]) for carrier in CARRIERS},
        }}
        if status == "success":
            result = object()
            changed, returned = asyncio.run(callback.async_logging_hook(kwargs, result, "acompletion"))
            assert changed is kwargs and returned is result
        asyncio.run(getattr(callback, "async_log_" + status + "_event")(kwargs, None, 0, 1))
        assert kwargs["standard_logging_object"]["model"] == "requested-alias"
        assert kwargs["standard_logging_object"]["completion_tokens"] == (7 if status == "success" else 0)
        record = json.loads(capsys.readouterr().out)
        assert {key: record[key] for key in ("agent", "tier", "privacy_class", "model", "accelerator")} == {
            "agent": "fixture-agent", "tier": 0, "privacy_class": privacy, "model": model, "accelerator": accelerator,
        }
        for target in (metadata, kwargs["standard_logging_object"]["metadata"]):
            for carrier in CARRIERS:
                assert target[carrier]["model"] == model
                assert target[carrier]["accelerator"] == accelerator
        # Native Prometheus custom labels read the flattened spend-log metadata.
        assert kwargs["standard_logging_object"]["metadata"]["spend_logs_metadata"]["accelerator"] == accelerator


def test_logging_hook_does_not_trust_request_identity_without_a_deployment(module):
    kwargs = {"litellm_params": {"metadata": {"model": "spoofed", "accelerator": "spoofed"}},
              "standard_logging_object": {"model": "requested-alias", "metadata": {}}}
    module["apply_deployment_contract"](kwargs)
    contract = kwargs["standard_logging_object"]["metadata"]["requester_metadata"]
    assert contract == {"model": None, "accelerator": None}
