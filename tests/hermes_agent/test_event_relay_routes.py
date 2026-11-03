"""Contract tests: the relay's Dify routes in the role defaults against each workflow's start variables."""
import json

import pytest
import yaml

from _event_relay_shared import MOD, ROLE

DEFAULTS = ROLE / "defaults" / "main" / "53-event-relay.yml"

# Required start variables from agents/dify/<name>.dsl.yml on ai-llm-prompts origin/main.
DSL_REQUIRED = {
    "ci-fixer": ["repo", "ref", "head_sha", "actor", "event", "strength", "mode", "check_run_id"],
    "code-simplifier": ["repo", "ref", "head_sha", "actor", "event", "strength", "mode", "pr_number"],
    "docs-updater": ["repo"],
    "doc-contradiction-finder": ["event", "action", "merged", "org", "repo", "pr_number"],
}


def _data():
    return yaml.safe_load(DEFAULTS.read_text())


def _routes():
    return {route["name"]: route for route in _data()["hermes_agent_event_relay_routes"]}


def test_defaults_declare_each_dify_route():
    assert set(_routes()) == set(DSL_REQUIRED)


@pytest.mark.parametrize("name", sorted(DSL_REQUIRED))
def test_required_list_matches_dsl(name):
    assert _routes()[name]["required"] == DSL_REQUIRED[name]


@pytest.mark.parametrize("name", sorted(DSL_REQUIRED))
def test_every_required_input_is_supplied(name):
    route = _routes()[name]
    supplied = set(route.get("inputs", {})) | set(route.get("const", {})) | {"event"}
    assert set(route["required"]) <= supplied


def test_no_route_sends_explicit_strength_or_live_mode():
    for name, route in _routes().items():
        const = route.get("const", {})
        assert "explicit" not in const.values(), name
        assert const.get("mode", "shadow") == "shadow", name


def test_each_route_key_is_declared_in_dify_keys():
    keys = _data()["hermes_agent_event_relay_dify_keys"]
    for name, route in _routes().items():
        assert route["app_key_env"] in keys, name


def test_finder_route_loads_from_defaults_only_with_its_key():
    finder = _routes()["doc-contradiction-finder"]
    assert [r["name"] for r in MOD.load_routes(json.dumps([finder]), {
        "RELAY_DIFY_KEY_DOC_CONTRADICTION_FINDER": "k4"})] == ["doc-contradiction-finder"]
    assert MOD.load_routes(json.dumps([finder]), {}) == []
