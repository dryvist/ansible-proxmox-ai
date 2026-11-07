"""Exercise owner membership and isolated transfer failures using native APIs."""

import pytest

from test_privacy_grants import MARKER, run_tasks
from test_teamless_key_policy import team_api


@pytest.mark.parametrize("tier", [0, 1, 2])
def test_owned_public_key_adds_destination_member_before_team_change(tmp_path, tier):
    key = {"token": MARKER, "key_alias": "fixture-public", "models": ["fixture-retaining"],
           "user_id": "fixture-owner", "team_id": f"tier-{tier}",
           "metadata": {"privacy_class": "public"}}
    target = f"tier-{tier}-public"
    desired = {"alias": key["key_alias"], "value": MARKER, "team_id": target,
               "models": key["models"], "privacy_class": "public"}
    with team_api(team_info={"models": ["fixture-local", "fixture-retaining"]},
                  keys=[key], users=[key["user_id"]]) as (port, writes):
        variables = {
            "llm_router_api_port": port, "llm_router_master_key": "sk-fixture-master",
            "llm_router_virtual_keys": [desired],
            "llm_router_existing_keys": {"json": {"keys": [key]}},
        }
        result = run_tasks(tmp_path, [], "reconcile-teamless-keys.yml", **variables)
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert key["team_id"] == target
        member = ("/team/member_add", {"team_id": target, "member": {"role": "user", "user_id": key["user_id"]}})
        transfer = ("/key/update", {"key": MARKER, "team_id": target})
        assert writes.index(member) < writes.index(transfer)
        assert "Added owner membership" in output
        assert MARKER not in output
        count = len(writes)
        result = run_tasks(tmp_path, [], "reconcile-teamless-keys.yml", **variables)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(writes) == count


@pytest.mark.parametrize("managed", [False, True])
def test_owned_teamless_key_adds_member_before_transfer(tmp_path, managed):
    key = {"token": MARKER, "key_alias": "fixture-owned", "models": ["fixture-retaining"],
           "user_id": "fixture-owner"}
    target = "tier-0" if managed else "default-settings"
    desired = {"alias": key["key_alias"], "value": MARKER,
               "team_id": target, "models": ["fixture-local"]}
    with team_api(keys=[key], users=[key["user_id"]]) as (port, writes):
        variables = {
            "llm_router_api_port": port, "llm_router_master_key": "sk-fixture-master",
            "llm_router_virtual_keys": [desired] if managed else [],
            "llm_router_existing_keys": {"json": {"keys": [key]}},
        }
        result = run_tasks(tmp_path, [], "reconcile-teamless-keys.yml", **variables)
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert key["team_id"] == target
        assert key["models"] == ["fixture-local"]
        member = ("/team/member_add", {"team_id": target, "member": {"role": "user", "user_id": key["user_id"]}})
        assert member in writes
        transfer = ("/key/update", {"key": MARKER, "team_id": target})
        assert writes.index(member) < writes.index(transfer)
        assert "Added owner membership for teamless key 1" in output
        assert MARKER not in output
        count = len(writes)
        result = run_tasks(tmp_path, [], "reconcile-teamless-keys.yml", **variables)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(writes) == count


def test_missing_owner_and_rejected_transfer_do_not_abort_other_keys(tmp_path):
    keys = [
        {"token": MARKER + "-missing", "user_id": "fixture-missing", "key_alias": "fixture-missing"},
        {"token": MARKER + "-reject", "models": ["fixture-local"]},
        {"token": MARKER + "-member", "user_id": "fixture-member"},
        {"token": MARKER + "-bare"},
    ]
    members = {"default-settings": ["fixture-member"]}
    with team_api(keys=keys, users=["fixture-member"], members=members,
                  reject_keys=[keys[1]["token"]]) as (port, writes):
        result = run_tasks(
            tmp_path, [], "reconcile-teamless-keys.yml",
            llm_router_api_port=port, llm_router_master_key="sk-fixture-master",
            llm_router_virtual_keys=[], llm_router_existing_keys={"json": {"keys": keys}},
        )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "team_id" not in keys[0]
    assert "team_id" not in keys[1]
    assert keys[2]["team_id"] == keys[3]["team_id"] == "default-settings"
    assert "Skipped teamless key 1" in output
    assert "Skipped teamless key 2" in output
    assert not any(path == "/team/member_add" for path, _ in writes)
    assert MARKER not in output


def test_skipped_managed_key_is_not_retried_by_seed_reconciliation(tmp_path):
    key = {"alias": "fixture-skipped", "value": MARKER, "models": ["fixture-local"], "team_id": "tier-0"}
    with team_api(keys=[{"token": MARKER}]) as (port, writes):
        result = run_tasks(
            tmp_path, [], "reconcile-seeded-key-policy.yml",
            llm_router_api_port=port, llm_router_master_key="sk-fixture-master",
            llm_router_virtual_keys=[key], _llm_router_teamless_skipped_aliases=[key["alias"]],
            llm_router_existing_keys={"json": {"keys": [{"key_alias": key["alias"]}]}},
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert writes == []
