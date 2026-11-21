"""The triage apps declare every variable their catalog DSL leaves empty, and keep the DSL's shadow mode."""

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
}
SECRET = {"OPENBAO_ROLE_ID", "OPENBAO_SECRET_ID", "SLACK_AGENT_BOT_TOKEN", "DIFY_DATASET_API_KEY", "LITELLM_ROUTER_KEY"}


def test_triage_apps_declare_exactly_their_empty_variables_and_no_mode():
    for app, expected in EXPECTED.items():
        entries = GROUP["dify_docker_agent_app_variables"][app]
        names = [entry["name"] for entry in entries]
        assert len(names) == len(set(names)), app
        assert set(names) == expected, app
        assert not any(name.endswith("_MODE") for name in names), app
        assert all(entry["secret"] for entry in entries if entry["name"] in SECRET), app
