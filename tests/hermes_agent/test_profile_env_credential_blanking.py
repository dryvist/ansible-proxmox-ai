"""hermes-env-profile.j2 blanks every credential a profile is not granted.

Split out of test_profile_contract.py (over the file token budget) — this is
one self-contained render-and-assert test, not several sharing state with the
rest of that module, so the split is a clean seam rather than a forced one.
"""

from __future__ import annotations

from test_profile_contract import _RENDER_CONTEXT, ROLE_ROOT, _jinja_env, _profiles


def test_profile_env_template_blanks_every_ungranted_credential() -> None:
    env = _jinja_env()
    src = (ROLE_ROOT / "templates" / "hermes-env-profile.j2").read_text()
    context = dict(_RENDER_CONTEXT)
    context.update(
        hermes_agent_model_api_key="MODELKEY",
        # Empty — the mandatory filter is stubbed to a passthrough above, so
        # a missing field renders blank. Router-key selection itself is
        # pinned in test_profile_router_key_selection.py, not here.
        bao_apps_secrets={},
        hermes_agent_slack_bot_token="xoxb-x",
        hermes_agent_slack_app_token="xapp-x",
        hermes_agent_slack_allowed_users="U1",
        hermes_agent_slack_home_channel="C1",
        hermes_agent_slack_home_channel_name="home",
        hermes_agent_splunk_mcp_token="SPLUNKTOK",
        hermes_agent_zammad_url="https://zammad.example.com",
        hermes_agent_zammad_api_token="ZAMTOK",
        hermes_agent_wiki_enabled=True,
        hermes_agent_wiki_path="/var/lib/hermes/wiki",
        hermes_agent_github_read_token="READTOK",
        # Rendered into the context but wired to NOTHING in the profile
        # template — see the GH_PAT_WRITE_PROJECT_ISSUES assertion below.
        hermes_agent_github_issues_pat="WRITETOK",
    )
    always_blank = ("GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "CONTEXT7_API_KEY")
    section_keys = {
        "slack": ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"),
        "splunk": ("SPLUNK_MCP_URL", "SPLUNK_MCP_TOKEN"),
        "zammad": ("ZAMMAD_URL", "ZAMMAD_API_TOKEN"),
        "github": ("GH_PAT_WRITE_PROJECT_ISSUES",),
    }

    for profile in _profiles():
        # Router-key selection is pinned in test_profile_router_key_selection.py;
        # here it only needs a non-empty value so the credential-blanking
        # assertions below aren't polluted by an unrelated mandatory() raise.
        profile_context = dict(context)
        profile_context["bao_apps_secrets"] = {
            f"hermes_{profile['name'].replace('-', '_')}_llm_router_key": "MODELKEY"
        }
        rendered = env.from_string(src).render(hermes_agent_profile=profile, **profile_context)
        values = dict(
            line.split("=", 1) for line in rendered.splitlines() if "=" in line and not line.startswith("#")
        )

        assert values["HERMES_AGENT_MODEL_API_KEY"] == "MODELKEY"
        for key in always_blank:
            assert values[key] == "", f"{profile['name']}: {key} must always be blank"
        for section, keys in section_keys.items():
            granted = section in profile["env"]
            for key in keys:
                if granted:
                    assert values[key] != "", f"{profile['name']}: {key} should be set ({section} granted)"
                else:
                    assert values[key] == "", f"{profile['name']}: {key} must be blank ({section} not granted)"
        # WIKI_PATH is not a credential and carries no 'env' grant — every
        # profile gets it whenever wiki is enabled, same as the default
        # profile's hermes-env.j2. Without this, a profile that DOES get the
        # research/llm-wiki skill (tasks/profiles.yml) would have the skill on
        # disk but no path for it to read.
        assert values["WIKI_PATH"] == "/var/lib/hermes/wiki", (
            f"{profile['name']}: WIKI_PATH must be set whenever hermes_agent_wiki_enabled is true"
        )
        # The read-only contract, asserted on the VALUE rather than the key:
        # GH_PAT_WRITE_PROJECT_ISSUES is the variable the bundled
        # dryvist/github-issues skill authenticates with, so a github-granted
        # profile has to render it — but it must carry the read-only token.
        # If it ever rendered the default profile's read/write PAT instead,
        # every "read-only" claim on that profile would be prompt-deep only.
        assert values["GH_PAT_WRITE_PROJECT_ISSUES"] != "WRITETOK", (
            f"{profile['name']}: the read/write issues PAT must never reach a profile .env"
        )
