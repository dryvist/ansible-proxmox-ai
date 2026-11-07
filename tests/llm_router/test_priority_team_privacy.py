"""Verify priority team grants through the role gate and native API requests."""

import pytest

from test_privacy_grants import run_tasks
from test_teamless_key_policy import team_api


@pytest.mark.parametrize("tier", [0, 1, 2])
@pytest.mark.parametrize("models", [["fixture-retaining"], ["fixture-free"], []])
@pytest.mark.parametrize("public", [False, True])
def test_every_priority_team_requires_public_for_unsafe_grants(tmp_path, tier, models, public):
    team = {"team_id": f"tier-{tier}", "tier": tier, "priority": tier + 1, "models": models}
    if public:
        team["privacy_class"] = "public"
    result = run_tasks(
        tmp_path, [], "assert-privacy-grants.yml", llm_router_priority_tiers=[team],
    )
    assert (result.returncode == 0) == public, result.stdout + result.stderr
    if not public:
        assert f"tier-{tier}" in result.stdout
        assert "grants require privacy class public" in result.stdout


@pytest.mark.parametrize("tier", [0, 1, 2])
@pytest.mark.parametrize("exists", [False, True])
def test_priority_teams_create_and_reconcile_zdr_models(tmp_path, tier, exists):
    current = {"models": [], "metadata": {"ui": "preserved"}, "router_settings": {"num_retries": 2}}
    with team_api(current if exists else None) as (port, writes):
        result = run_tasks(
            tmp_path, [], "seed-priority-teams.yml",
            llm_router_priority_tiers=[{"tier": tier, "team_id": f"tier-{tier}", "priority": tier + 1}],
            llm_router_api_port=port, llm_router_master_key="sk-fixture-master",
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(writes) == 1
    path, body = writes[0]
    assert path == ("/team/update" if exists else "/team/new")
    assert body["models"] == ["fixture-local"]
    assert body["metadata"]["tags"] == [f"&tier-{tier}"] + (["&local"] if tier == 2 else [])
    assert body["router_settings"]["enable_tag_filtering"] is True
    if exists:
        assert body["metadata"]["ui"] == "preserved"
        assert body["router_settings"]["num_retries"] == 2
    if tier == 2:
        assert body["router_settings"]["fallbacks"] == []
        assert body["router_settings"]["timeout"] == 45600


def test_public_priority_team_keeps_declared_grants_and_privacy(tmp_path):
    with team_api() as (port, writes):
        result = run_tasks(
            tmp_path, [], "seed-priority-teams.yml",
            llm_router_priority_tiers=[{
                "tier": 1, "team_id": "tier-1", "priority": 10,
                "privacy_class": "public", "models": ["fixture-retaining", "fixture-free"],
            }],
            llm_router_api_port=port, llm_router_master_key="sk-fixture-master",
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert writes[0][1]["models"] == ["fixture-retaining", "fixture-free"]
    assert writes[0][1]["metadata"]["privacy_class"] == "public"
