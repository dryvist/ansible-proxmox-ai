"""Contract tests: the relay's Dify routes in the role defaults against each workflow's start variables."""
import json

import pytest
import yaml

from _event_relay_shared import MOD, ROLE

DEFAULTS = ROLE / "defaults" / "main" / "53-event-relay.yml"
ENV = {"RELAY_DIFY_KEY_CI_FIXER": "k1"}

# Required start variables copied from ai-llm-prompts 31eed96, agents/dify/<name>.dsl.yml.
# The DSLs live in another repo, so these lists are hand-copied; drift is not checked here.
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
        # strength and mode must never come from the payload either.
        assert not {"strength", "mode"} & set(route.get("inputs", {})), name


def test_const_cannot_override_the_envelope_event():
    route = _routes()["ci-fixer"]
    assert "event" not in route.get("const", {})
    assert MOD.dify_inputs("check_run", b"{}", {}, const={"event": "forged"})["event"] == "check_run"


def test_load_rejects_payload_mapped_strength_mode_or_event(capsys):
    ci = _routes()["ci-fixer"]
    patches = [{"strength": "check_run.conclusion"}, {"mode": "check_run.conclusion"}, {"event": "action"}]
    routes = [dict(ci, inputs={**ci["inputs"], **patch}) for patch in patches]
    assert MOD.load_routes(json.dumps(routes), ENV) == []
    assert capsys.readouterr().out.count("inputs must not map event, strength or mode") == 3


def test_load_rejects_explicit_strength_live_mode_and_overlapping_constants(capsys):
    ci = _routes()["ci-fixer"]
    bad_consts = [
        {"strength": "explicit"}, {"mode": "live"}, {"event": "forged"}, {"repo": "other/repo"},
    ]
    for const in bad_consts:
        assert MOD.load_routes(json.dumps([dict(ci, const={**ci["const"], **const})]), ENV) == [], const
    out = capsys.readouterr().out
    assert out.count("const strength must be inferred") == 1
    assert out.count("const mode must be shadow") == 1
    assert out.count("const names must not repeat an input name or event") == 2


def test_ci_fixer_loads_from_defaults_with_its_key():
    ci = _routes()["ci-fixer"]
    assert [r["name"] for r in MOD.load_routes(json.dumps([ci]), ENV)] == ["ci-fixer"]


def test_each_route_key_is_declared_in_dify_keys():
    keys = _data()["hermes_agent_event_relay_dify_keys"]
    for name, route in _routes().items():
        assert route["app_key_env"] in keys, name


def test_finder_route_loads_from_defaults_only_with_its_key():
    finder = _routes()["doc-contradiction-finder"]
    assert [r["name"] for r in MOD.load_routes(json.dumps([finder]), {
        "RELAY_DIFY_KEY_DOC_CONTRADICTION_FINDER": "k4"})] == ["doc-contradiction-finder"]
    assert MOD.load_routes(json.dumps([finder]), {}) == []
