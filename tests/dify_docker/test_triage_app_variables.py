"""Tracker apps declare every variable their catalog DSL leaves empty, keep the DSL's shadow mode,
and take credentials only from their own per-app leaf or their own AppRole."""

from __future__ import annotations

from pathlib import Path

import yaml

GROUP = yaml.safe_load((Path(__file__).resolve().parents[2] / "inventory/group_vars/dify_group.yml").read_text(encoding="utf-8"))
SHARED = {"OPENBAO_ADDR", "OPENBAO_ROLE_ID", "OPENBAO_SECRET_ID", "SLACK_AGENT_BOT_TOKEN", "SLACK_API_BASE",
          "AGENT_DIGEST_CHANNEL", "AGENT_FAILURE_CHANNEL", "AGENT_DASHBOARD_URL"}
# The empty-valued environment variables of each app in the pinned catalog DSL.
EXPECTED = {
    "vikunja-triage": SHARED | {
        "VIKUNJA_TRIAGE_ALLOWED_PROJECTS", "VIKUNJA_TRIAGE_READONLY_PROJECTS", "VIKUNJA_TRIAGE_RANK_ONLY_PROJECTS",
        "VIKUNJA_TRIAGE_DENIED_PROJECTS", "GITHUB_READ_TOKEN_PATH", "GITHUB_API_BASE", "GITHUB_WEB_HOST", "DIFY_API_BASE",
        "DIFY_DATASET_API_KEY", "TRIAGE_EMBEDDING_PROVIDER", "TRIAGE_EMBEDDING_MODEL"},
    "zammad-triage": SHARED | {"ZAMMAD_API_BASE", "ZAMMAD_TOKEN_PATH", "LITELLM_ROUTER_KEY"},
    "zammad-clerk": {"ZAMMAD_BASE_URL", "ZAMMAD_TOKEN"},
}
SECRET = {"OPENBAO_ROLE_ID", "OPENBAO_SECRET_ID", "SLACK_AGENT_BOT_TOKEN", "DIFY_DATASET_API_KEY", "LITELLM_ROUTER_KEY",
          "ZAMMAD_TOKEN"}
APPROLE = {"vikunja-triage": "agent-vikunja-triage", "zammad-triage": "agent-zammad-triage"}
LEAF = "{{ dify_docker_openbao_path }}/{{ dify_docker_agent_app_leaf_segment }}/"


def entries(app):
    return GROUP["dify_docker_agent_app_variables"][app]


def test_tracker_apps_declare_exactly_their_empty_variables_and_no_mode():
    for app, expected in EXPECTED.items():
        names = [entry["name"] for entry in entries(app)]
        assert len(names) == len(set(names)), app
        assert set(names) == expected, app
        assert not any(name.endswith("_MODE") for name in names), app
        assert all(entry["secret"] for entry in entries(app) if entry["name"] in SECRET), app


def test_each_entry_names_exactly_one_source():
    for app in EXPECTED:
        for entry in entries(app):
            sources = {"value", "kv", "approle_role_id", "approle_secret_id"} & set(entry)
            assert len(sources) == 1, (app, entry["name"])


def test_secrets_come_from_the_apps_own_leaf_or_approle_never_env():
    for app in EXPECTED:
        for entry in entries(app):
            if entry["name"] not in SECRET:
                continue
            assert "value" not in entry, (app, entry["name"])
            if "kv" in entry:
                assert entry["kv"]["path"] == LEAF + app, (app, entry["name"])
            else:
                source = entry.get("approle_role_id") or entry.get("approle_secret_id")
                assert source == {"approle": APPROLE[app]}, (app, entry["name"])


def test_no_entry_reads_a_converge_env_key_for_credentials_or_addresses():
    for app in EXPECTED:
        for entry in entries(app):
            value = str(entry.get("value", ""))
            assert "DIFY_TRIAGE_" not in value and "DIFY_ZAMMAD_TRIAGE_" not in value, (app, entry["name"])
            assert "_OPENBAO_" not in value, (app, entry["name"])
