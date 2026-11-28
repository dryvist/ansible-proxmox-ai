from __future__ import annotations

import json
import re
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "roles/llm_router/templates"
DEFAULTS_DIR = REPO_ROOT / "roles/llm_router/defaults/main"

ACCEPTANCE_PROFILES = (
    "vikunja-clerk-acceptance",
    "vikunja-triage-acceptance",
    "vikunja-nightly-acceptance",
    "vikunja-windows-acceptance",
)
AGENT_URL = "/v1"
FEATURE_GATES: dict[str, str] = {}


def _env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
        trim_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(
        pattern, replacement, str(value)
    )
    env.filters["to_json"] = lambda value: json.dumps(
        value if isinstance(value, (dict, list, tuple)) else list(value)
    )
    env.filters["bool"] = lambda value: (
        value
        if isinstance(value, bool)
        else str(value).lower() in {"true", "yes", "on", "1"}
    )
    env.filters["dict2items"] = lambda value: [
        {"key": key, "value": item} for key, item in value.items()
    ]
    env.filters["combine"] = lambda value, other: {**value, **other}
    env.globals["lookup"] = lambda term, name, default=None: FEATURE_GATES.get(
        name, default
    )
    return env


def _load_56f() -> dict:
    return yaml.safe_load((DEFAULTS_DIR / "56f-acceptance-candidates.yml").read_text())


def _light_registry_fixture() -> list[dict]:
    path = REPO_ROOT / "tests/llm_router/test_mcp_gateway_contract.yml"
    entries = next(
        play["vars"]["_non_chat_fixtures"]
        for play in yaml.safe_load(path.read_text())
        if "_non_chat_fixtures" in play.get("vars", {})
    )
    return [entry for entry in entries if entry.get("tier") == "light"]


def _candidate_vars(
    normal_alias: str = "medium", light_models: list[dict] | None = None
) -> dict:
    data = _load_56f()
    light_models = _light_registry_fixture() if light_models is None else light_models
    context = {
        "hermes_brain_model": normal_alias,
        "llm_router_light_models": light_models,
        "llm_router_vikunja_clerk_key_policy": {"acceptance": {"key_alias": "evals"}},
    }
    env = _env()
    def render(name: str):
        return yaml.safe_load(env.from_string(data[name]).render(**context))

    for key in (
        "_llm_router_acceptance_weak_target",
        "llm_router_acceptance_candidate_aliases",
    ):
        context[key] = render(key)
    context["llm_router_acceptance_candidate_agents"] = data[
        "llm_router_acceptance_candidate_agents"
    ]
    for key in (
        "_llm_router_acceptance_candidate_route_names",
        "_llm_router_acceptance_profile_key_aliases",
        "_llm_router_acceptance_candidate_models_by_key",
    ):
        context[key] = render(key)
    context.pop("llm_router_vikunja_clerk_key_policy")
    return context


def _candidate_aliases(normal_alias: str = "medium") -> list[str]:
    return _candidate_vars(normal_alias)["llm_router_acceptance_candidate_aliases"]


def _bearer_secrets(profiles: list[dict]) -> dict:
    return {
        "hermes_" + profile["name"].replace("-", "_") + "_api_server_key": "placeholder"
        for profile in profiles
        if profile.get("bearer", True)
    }


def _render_agent_list(
    enabled: bool | None,
    profiles: list[dict],
    candidate_vars: dict | None = None,
) -> list[dict]:
    env = _env()
    priority_defaults = yaml.safe_load(
        (DEFAULTS_DIR / "56d-priority-tiers.yml").read_text()
    )
    kwargs = dict(
        llm_router_hermes_agents={
            "hermes-agent": {"api_url": AGENT_URL, "model_name": "hermes-agent"}
        },
        llm_router_hermes_agent_identities={"hermes-agent": "hermes-agent"},
        llm_router_hermes_identity_bearers={"hermes-agent": "placeholder"},
        bao_apps_secrets=_bearer_secrets(profiles),
        hermes_agent_profiles=profiles,
        llm_router_hermes_agent_timeout=120,
        llm_router_hermes_agent_context_window=32768,
        llm_router_priority_tiers=priority_defaults["llm_router_priority_tiers"],
    )
    if enabled is not None:
        kwargs["llm_router_acceptance_candidates_enabled"] = enabled
    if candidate_vars is not None:
        kwargs.update(candidate_vars)
    rendered = env.get_template("model-list-hermes-agents.yaml.j2").render(**kwargs)
    return yaml.safe_load("model_list:\n" + rendered)["model_list"]


def test_default_candidate_alias_tracks_hermes_brain_model():
    assert _candidate_aliases("fixture-normal-model") == [
        "fixture-normal-model",
        "c12-weak",
    ]


def _gated_profiles() -> list[dict]:
    return [{"name": name, "locked": True} for name in ACCEPTANCE_PROFILES]


def test_switch_off_renders_no_candidate_routes():
    deployments = _render_agent_list(False, _gated_profiles(), _candidate_vars())
    names = [deployment["model_name"] for deployment in deployments]

    assert not any("-candidate-" in name for name in names)
    # Normal routes remain.
    assert {f"agent-{name}" for name in ACCEPTANCE_PROFILES} <= set(names)


def test_switch_on_renders_agent_alias_matrix_for_gated_profiles():
    aliases = _candidate_aliases()
    agents = _load_56f()["llm_router_acceptance_candidate_agents"]
    deployments = _render_agent_list(True, _gated_profiles(), _candidate_vars())
    names = [deployment["model_name"] for deployment in deployments]
    expected_candidates = {
        f"agent-{agent}-candidate-{alias}"
        for agent, profile in agents.items()
        if profile in ACCEPTANCE_PROFILES
        for alias in aliases
    }

    # Exact profile x alias matrix.
    assert set(names) == (
        {"hermes-agent"}
        | {f"agent-{name}" for name in ACCEPTANCE_PROFILES}
        | expected_candidates
    )
    assert len(expected_candidates) == len(ACCEPTANCE_PROFILES) * len(aliases)

    for deployment in deployments:
        if "-candidate-" not in deployment["model_name"]:
            continue
        profile = next(
            name
            for name in ACCEPTANCE_PROFILES
            if f"/p/{name}/v1" in deployment["litellm_params"]["api_base"]
        )
        alias = deployment["model_name"].rsplit("-candidate-", 1)[1]
        params = deployment["litellm_params"]
        normal = next(
            item for item in deployments if item["model_name"] == f"agent-{profile}"
        )
        # Same profile parameters, model alias, and provider override.
        expected_params = normal["litellm_params"].copy()
        expected_params["model"] = f"hosted_vllm/{alias}"
        expected_params["extra_body"] = {"provider": "custom:hermes-shared"}
        assert params == expected_params
        assert deployment["model_info"] == normal["model_info"]


def test_profile_gated_off_or_missing_bearer_renders_no_candidate_entry():
    agents = _load_56f()["llm_router_acceptance_candidate_agents"]
    candidate_names = {
        f"agent-{agent}-candidate-{alias}"
        for agent, profile in agents.items()
        for alias in _candidate_aliases()
        if profile in ACCEPTANCE_PROFILES
    }

    # Not locked: the profile loop skips it entirely.
    unlocked = _render_agent_list(
        True,
        [{"name": "vikunja-clerk-acceptance", "locked": False}],
        _candidate_vars(),
    )
    assert not any(
        deployment["model_name"] in candidate_names
        for deployment in unlocked
    )

    # Locked but no resolved bearer: the entry is skipped.
    nobearer = _render_agent_list(
        True,
        [{"name": "vikunja-clerk-acceptance", "locked": True, "bearer": False}],
        _candidate_vars(),
    )
    assert not any(
        deployment["model_name"] in candidate_names
        for deployment in nobearer
    )

    # Feature gate off via the inventory variable: also skipped.
    FEATURE_GATES["llm_router_test_gate"] = "false"
    try:
        gated = _render_agent_list(
            True,
            [
                {
                    "name": "vikunja-clerk-acceptance",
                    "locked": True,
                    "feature_gate": "llm_router_test_gate",
                }
            ],
            _candidate_vars(),
        )
    finally:
        FEATURE_GATES.clear()
    assert not any(
        deployment["model_name"] in candidate_names for deployment in gated
    )


def _render_alias_pairs(
    enabled: bool | None, candidate_vars: dict | None = None
) -> list[list[str]]:
    data = yaml.safe_load((DEFAULTS_DIR / "50-direct-routes.yml").read_text())
    kwargs = dict(
        llm_router_model_registry=[],
        llm_router_local_registry_tiers=[],
        llm_router_registry_enabled=[],
        llm_router_opencode_stable_alias_entries=[],
        llm_router_long_context_alias_ids=[],
        llm_router_benchmark_only_model_names=[],
        llm_router_rendered_capability_names=[],
        _llm_router_declared_primary={"route_group": "medium"},
        llm_router_small_capability_name="small",
        llm_router_primary_large_model="medium",
        llm_router_vikunja_clerk_key_policy={},
        llm_router_direct_route_alias_pairs=[],
        llm_router_subagent_public_alias_pairs=[],
    )
    if enabled is not None:
        kwargs["llm_router_acceptance_candidates_enabled"] = enabled
    if candidate_vars is not None:
        kwargs["_llm_router_acceptance_weak_target"] = candidate_vars[
            "_llm_router_acceptance_weak_target"
        ]
    rendered = _env().from_string(data["llm_router_alias_pairs"]).render(**kwargs)
    return yaml.safe_load(rendered)


def _render_light_model_list(light_models: list[dict]) -> list[dict]:
    env = _env()
    env.globals.update(
        deployment_tags=lambda *_: "[]",
        litellm_model=lambda *_: "openai/fixture",
        local_bounds=lambda *_: "\n      num_retries: 0",
        local_extra_body=lambda *_: "",
        light_window=lambda *_: "",
        delegation_hints=lambda *_: "",
    )
    rendered = env.get_template("model-list-light.yaml.j2").render(
        llm_router_light_models=light_models,
        llm_router_priority_tiers=[{"tier": 1, "priority": 1, "team_id": "default"}],
        llm_router_llm_fast_enabled=False,
        llm_router_llm_light_base_url="/v1",
        llm_router_light_api_key="fixture",
        llm_router_llm_light_accelerator="cpu",
        llm_router_local_max_parallel_requests=1,
    )
    return yaml.safe_load("model_list:\n" + rendered)["model_list"]


def test_weak_alias_uses_first_nonembedding_light_route():
    candidate_vars = _candidate_vars()
    rendered_names = {
        d["model_name"]
        for d in _render_light_model_list(candidate_vars["llm_router_light_models"])
    }
    pair = [candidate_vars["_llm_router_acceptance_weak_target"], "c12-weak"]

    assert candidate_vars["_llm_router_acceptance_weak_target"] == "fixture-reranker"
    assert pair in _render_alias_pairs(True, candidate_vars)
    assert pair[0] in rendered_names
    assert candidate_vars["llm_router_acceptance_candidate_aliases"] == [
        "medium",
        "c12-weak",
    ]
    assert pair not in _render_alias_pairs(False, candidate_vars)


def test_no_light_route_omits_weak_pair_and_candidate_alias():
    candidate_vars = _candidate_vars(light_models=[])

    assert not candidate_vars["_llm_router_acceptance_weak_target"]
    assert candidate_vars["llm_router_acceptance_candidate_aliases"] == ["medium"]
    assert not any(
        pair[1] == "c12-weak"
        for pair in _render_alias_pairs(True, candidate_vars)
    )


def test_candidate_aliases_deduplicate_when_normal_equals_target():
    candidate_vars = _candidate_vars(
        normal_alias="c12-weak",
        light_models=[{"client_model_id": "fixture-route", "route_group": "c12-weak"}],
    )

    assert candidate_vars["_llm_router_acceptance_weak_target"] == "c12-weak"
    assert candidate_vars["llm_router_acceptance_candidate_aliases"] == ["c12-weak"]


def _render_key_scopes(**overrides) -> dict[str, dict]:
    data = yaml.safe_load((DEFAULTS_DIR / "59-key-scopes.yml").read_text())
    kwargs = dict(
        _llm_router_hermes_locked_profile_models=["agent-vikunja-clerk-acceptance"],
        _llm_router_vikunja_clerk_managed_models=[],
        llm_router_hermes_clerk_caller_key_aliases=["vikunja-clerk"],
        llm_router_vikunja_clerk_key_policy={
            "enabled": True,
            "acceptance": {
                "key_alias": "evals",
                "aliases": {},
            "models": [],
            },
            "outside_trusted": {"aliases": {}, "models": []},
        },
        _llm_router_trusted_caller_tiers=["trusted"],
        ai_callers={},
        agent_callers_default_tier="untrusted",
        _llm_router_hermes_profile_keys=[],
        _llm_router_static_virtual_keys=[],
        _llm_router_target_virtual_keys=[],
        _llm_router_workstation_virtual_keys=[],
        _llm_router_agent_keys=[
            {"alias": "evals", "models": ["evals-base"]},
            {"alias": "vikunja-clerk", "models": ["clerk-base"]},
            {"alias": "unrelated-caller", "models": ["other-base"]},
        ],
    )
    kwargs.update(overrides)
    rendered = _env().from_string(data["_llm_router_declared_virtual_keys"]).render(
        **kwargs
    )
    return {key["alias"]: key for key in yaml.safe_load(rendered)}


def _candidate_profile_key_fixtures() -> list[dict]:
    profiles = _load_56f()["llm_router_acceptance_candidate_agents"].values()
    keys = [
        {"alias": f"hermes-{p}", "models": [f"base-{p}"]}
        for p in dict.fromkeys(profiles)
    ]
    return keys + [{"alias": "hermes-outside-acceptance", "models": ["outside-base"]}]


def test_candidate_names_granted_only_to_the_acceptance_key():
    keys = _render_key_scopes(
        llm_router_acceptance_candidates_enabled=True,
        **_candidate_vars(),
    )
    candidates = set(_candidate_vars()["_llm_router_acceptance_candidate_route_names"])

    assert candidates <= set(keys["evals"]["models"])
    for alias, key in keys.items():
        if alias == "evals":
            continue
        assert not (set(key.get("models", [])) & candidates)
        assert not any("-candidate-" in model for model in key.get("models", []))
    # Clerk grants remain intact.
    assert "agent-vikunja-clerk-acceptance" in keys["vikunja-clerk"]["models"]


def test_candidate_grant_defaults_off_when_switch_undefined():
    keys = _render_key_scopes()  # no candidate variables at all

    assert not any(
        "-candidate-" in model for model in keys["evals"].get("models", [])
    )


def test_candidate_grant_is_independent_of_clerk_variants_policy():
    policy = {
        "enabled": False,
        "acceptance": {"key_alias": "evals", "aliases": {}, "models": []},
    }
    candidate_vars = _candidate_vars()
    keys = _render_key_scopes(
        llm_router_vikunja_clerk_key_policy=policy,
        llm_router_acceptance_candidates_enabled=True,
        **candidate_vars,
    )
    candidates = set(candidate_vars["_llm_router_acceptance_candidate_route_names"])

    assert candidates <= set(keys["evals"]["models"])
    for alias, key in keys.items():
        if alias != "evals":
            assert not any("-candidate-" in model for model in key.get("models", []))


def test_candidate_aliases_for_mapped_profile_keys():
    vars = _candidate_vars()
    agents = vars["llm_router_acceptance_candidate_agents"]
    keys = _render_key_scopes(
        _llm_router_hermes_profile_keys=_candidate_profile_key_fixtures(),
        llm_router_acceptance_candidates_enabled=True,
        **vars,
    )

    for profile in set(agents.values()):
        assert set(vars["llm_router_acceptance_candidate_aliases"]) <= set(
            keys[f"hermes-{profile}"]["models"]
        )
    assert not (
        set(vars["llm_router_acceptance_candidate_aliases"])
        & set(keys["hermes-outside-acceptance"]["models"])
    )


def test_switch_off_key_scope_has_no_candidate_models():
    vars = _candidate_vars()
    keys = _render_key_scopes(
        _llm_router_hermes_profile_keys=_candidate_profile_key_fixtures(),
        llm_router_acceptance_candidates_enabled=False,
        **vars,
    )
    candidates = set(vars["llm_router_acceptance_candidate_aliases"]) | set(
        vars["_llm_router_acceptance_candidate_route_names"]
    )

    for key in keys.values():
        assert not (candidates & set(key.get("models", [])))
