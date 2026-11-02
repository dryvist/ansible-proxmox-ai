"""Keep GPU profiles aligned with the router's projected route groups."""

import yaml

from _gpu_registry_small_alternatives import SMALL_CANDIDATES
from test_gpu_registry import REGISTRY_DEFAULTS, REGISTRY_TASKS, _entries


def test_router_consumes_the_shared_gpu_profile_projection() -> None:
    defaults = yaml.safe_load(REGISTRY_DEFAULTS.read_text(encoding="utf-8"))
    tasks = yaml.safe_load(REGISTRY_TASKS.read_text(encoding="utf-8"))
    projection = next(
        task["ansible.builtin.set_fact"]
        for task in tasks
        if task.get("name") == "Resolve GPU router upstream ids from artifact references"
    )

    assert "llm_router_gpu_profile_registry" in projection
    assert "llm_router_registry_gpu_profiles" in projection["llm_router_gpu_profile_registry"]
    assert "llm_router_gpu_profile_registry" in defaults["llm_router_model_registry"]

    entries = _entries()
    aliases_by_profile = {
        entry["profile"]: entry.get("stable_aliases", []) for entry in entries
    }
    assert all(not aliases for aliases in aliases_by_profile.values())
    assert {entry["profile"]: entry.get("route_group") for entry in entries} == {
        "small": "small",
        "small-c4-64k": "small",
        "small-k2-horizon": "small-trial-gpu",
        **{profile_name: "small" for profile_name in SMALL_CANDIDATES},
        "medium-a": "medium",
        "qwen38-96k-11-fp8-mtp": "medium",
        "qwen38-27b-131k-1": "agent-long-context",
        **{entry["profile"]: None for entry in entries
           if entry["profile"] not in ({"small", "small-c4-64k", "medium-a",
                                        "qwen38-96k-11-fp8-mtp", "small-k2-horizon",
                                        "qwen38-27b-131k-1"}
                                      | set(SMALL_CANDIDATES))},
    }

    agent_lane = next(
        entry for entry in entries if entry["profile"] == "qwen38-27b-131k-1"
    )
    assert agent_lane["tags"] == ["agent-only"]
    assert agent_lane["order"] == 99
    assert agent_lane["max_parallel_requests"] == 1
    assert "stable_aliases" not in agent_lane
    assert "fallback_group" not in agent_lane
