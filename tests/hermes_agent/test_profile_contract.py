from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"

_RENDER_CONTEXT: dict[str, Any] = {
    "ansible_managed": "managed",
    "hermes_agent_model": "hermes-default",
    "hermes_agent_model_base_url": "https://llm.example.com/v1",
    "hermes_agent_model_api_mode": "chat_completions",
    "hermes_agent_model_context_length": 65536,
    "hermes_agent_model_max_tokens": 8192,
    "hermes_agent_memory_provider": "hindsight",
    "hermes_agent_log_level": "DEBUG",
    "hermes_agent_max_turns": 90,
    "hermes_agent_context_compression_enabled": True,
    "hermes_agent_context_compression_threshold": 0.75,
    "hermes_agent_compression_model": "hermes-default",
    "hermes_agent_kanban_goal_mode": True,
    "hermes_agent_kanban_goal_judge_model": "hermes-default",
    "hermes_agent_session_reset_at_hour": 4,
    "hermes_agent_session_reset_idle_minutes": 1440,
    "hermes_agent_mcp_tool_timeout_seconds": 180,
    "hermes_agent_docs_mcp_url": "https://mcp.example.com/docs",
    "hermes_agent_vikunja_mcp_url": "https://mcp.example.com/vikunja",
    "hermes_agent_nautobot_mcp_url": "https://mcp.example.com/nautobot",
    "hermes_agent_splunk_mcp_enabled": True,
    "hermes_agent_splunk_mcp_url": "https://mcp.example.com/splunk",
    "hermes_agent_docs_mcp_enabled": True,
    "hermes_agent_vikunja_mcp_enabled": False,
    "hermes_agent_nautobot_mcp_enabled": False,
    "hermes_agent_timezone": "UTC",
}


def _jinja_env() -> Environment:
    env = Environment(autoescape=False)
    env.filters["to_json"] = json.dumps
    env.filters["bool"] = bool
    env.filters["comment"] = lambda v: f"# {v}"
    return env


def _defaults() -> dict[str, Any]:
    return yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text())


def _group_vars_all() -> dict[str, Any]:
    """all.yml plus the tofu_data it now derives ai_llm_concurrency from.

    ai_llm_concurrency is itself a Jinja expression over tofu_data.constants
    (dryvist/tofu-proxmox's pipeline_constants.serving.llm_concurrency,
    published via the inventory_resolve role at playbook run time) rather
    than a bare int. tofu_data doesn't exist outside a real inventory load,
    so a minimal fixture stands in here — 1, matching the current published
    value — and ai_llm_concurrency is pre-rendered against it so _effective's
    single-pass render below sees a resolved int, the way Ansible's templar
    would after resolving both levels.
    """
    group_vars = yaml.safe_load(
        (REPO_ROOT / "inventory" / "group_vars" / "all.yml").read_text()
    )
    group_vars["tofu_data"] = {"constants": {"serving": {"llm_concurrency": 1}}}
    group_vars["ai_llm_concurrency"] = (
        _jinja_env().from_string(str(group_vars["ai_llm_concurrency"])).render(**group_vars)
    )
    return group_vars


def _effective(key: str) -> int:
    """Resolve a defaults value that may derive from a group_vars constant.

    The caps below are Jinja expressions over ai_llm_concurrency rather than
    bare ints, so comparing the raw YAML would assert the TEMPLATE TEXT and
    pass or fail for the wrong reason. Render it the way Ansible would and
    assert the value that actually reaches the config.
    """
    raw = _defaults()[key]
    if isinstance(raw, int):
        return raw
    rendered = _jinja_env().from_string(str(raw)).render(**_group_vars_all())
    return int(rendered)


def _profiles() -> list[dict[str, Any]]:
    return _defaults()["hermes_agent_profiles"]


def _cards() -> list[dict[str, Any]]:
    return _defaults()["hermes_agent_kanban_cards"]


def test_every_card_carries_an_explicit_assignee_key() -> None:
    for card in _cards():
        assert "assignee" in card, f"card {card.get('job')!r} has no assignee key"
        assert isinstance(card["assignee"], str)


def test_every_non_empty_assignee_names_a_real_profile() -> None:
    profile_names = {p["name"] for p in _profiles()}
    for card in _cards():
        assignee = card["assignee"]
        if assignee:
            assert assignee in profile_names, (
                f"card {card.get('job')!r} assignee {assignee!r} is not in "
                f"hermes_agent_profiles ({sorted(profile_names)})"
            )


def test_splunk_monitor_skill_cards_are_assigned_splunk_admin() -> None:
    # The one mechanical, unambiguous ownership rule: any card wired to the
    # splunk-monitor skill belongs to the splunk-admin profile — including a
    # retired/paused card, so there is no unexplained exception to this rule.
    for card in _cards():
        if "dryvist/splunk-monitor" in card.get("skills", []):
            assert card["assignee"] == "splunk-admin", (
                f"card {card.get('job')!r} carries dryvist/splunk-monitor but "
                f"is assigned {card['assignee']!r}, not splunk-admin"
            )


def test_profile_env_lists_never_grant_a_forbidden_credential_section() -> None:
    # Each profile's Must-NOT list, encoded as forbidden .env sections
    # (roles/hermes_agent/README.md "Operating profiles" table).
    forbidden_sections = {
        "splunk-admin": {"zammad", "github"},
        "homelab-admin": {"splunk", "github"},
    }
    for profile in _profiles():
        forbidden = forbidden_sections.get(profile["name"], set())
        granted = set(profile["env"])
        overlap = granted & forbidden
        assert not overlap, (
            f"profile {profile['name']!r} env list {sorted(granted)} grants "
            f"forbidden section(s) {sorted(overlap)}"
        )


def test_profile_mcp_lists_never_grant_a_forbidden_server() -> None:
    forbidden_mcp = {
        "splunk-admin": {"codex", "context7", "vikunja", "nautobot"},
        "homelab-admin": {"splunk", "codex", "context7"},
    }
    for profile in _profiles():
        forbidden = forbidden_mcp.get(profile["name"], set())
        granted = set(profile["mcp"])
        overlap = granted & forbidden
        assert not overlap, (
            f"profile {profile['name']!r} mcp list {sorted(granted)} grants "
            f"forbidden server(s) {sorted(overlap)}"
        )


def test_profile_skill_lists_never_grant_a_forbidden_skill() -> None:
    forbidden_skills = {
        "splunk-admin": {"dryvist/github-issues", "dryvist/zammad-incidents", "dryvist/docs-pr"},
        "homelab-admin": {"dryvist/splunk-monitor", "dryvist/github-issues", "dryvist/docs-pr"},
    }
    for profile in _profiles():
        forbidden = forbidden_skills.get(profile["name"], set())
        granted = set(profile["skills"])
        overlap = granted & forbidden
        assert not overlap, (
            f"profile {profile['name']!r} skill list {sorted(granted)} grants "
            f"forbidden skill(s) {sorted(overlap)}"
        )


def test_concurrency_sum_cap_is_pinned_to_todays_effective_ceiling() -> None:
    # Naming more profiles must never silently raise real concurrency — the
    # SUM cap stays at 1 (today's effective ceiling with per-profile cap 1
    # and a single profile) until an operator deliberately raises it.
    #
    # Asserts the EFFECTIVE value, not the literal: both caps now derive from
    # ai_llm_concurrency (inventory/group_vars/all.yml), the single definition
    # of serving concurrency. That makes this test strictly stronger — raising
    # ai_llm_concurrency now trips it too, which is correct, because raising
    # the sum cap is exactly the operator decision this test exists to gate.
    assert _effective("hermes_agent_kanban_max_in_progress") == 1
    assert _effective("hermes_agent_kanban_max_in_progress_per_profile") == 1


def test_every_profile_has_a_soul_addendum_template_on_disk() -> None:
    for profile in _profiles():
        addendum = ROLE_ROOT / "templates" / profile["soul_addendum_file"]
        assert addendum.is_file(), f"missing {addendum}"


def test_profiles_tasks_are_wired_before_the_kanban_card_reconcile() -> None:
    """Native-cron reframe: only docs-sync is still a Kanban card (its
    `assignee` needs a profile to already exist), so the task it must precede
    is now "Reconcile the docs-sync Kanban card cron", not the retired
    per-workload enqueuer reconcile."""
    tasks = (ROLE_ROOT / "tasks" / "main.yml").read_text()
    profiles_idx = tasks.index("Reconcile named Hermes operating profiles")
    kanban_idx = tasks.index("Reconcile the docs-sync Kanban card cron")
    assert profiles_idx < kanban_idx


def test_llm_wiki_skill_is_materializable_into_any_profile_that_opts_in() -> None:
    # research/llm-wiki ships from hermes_agent_install_dir (the hermes-agent
    # install itself), not the nix-hermes dryvist bundle the "scoped dryvist
    # skills" loop below copies from — so it needs its OWN copy task, or a
    # profile naming it in `skills` would get no error and no skill (silent
    # gap, not a loud one). Gated on the same hermes_agent_wiki_enabled flag
    # as the default profile's copy in tasks/main.yml.
    profiles_tasks = (ROLE_ROOT / "tasks" / "profiles.yml").read_text()
    assert "Materialize the llm-wiki skill for profile" in profiles_tasks
    assert "{{ hermes_agent_install_dir }}/skills/research/llm-wiki" in profiles_tasks
    assert (
        "when: hermes_agent_wiki_enabled | bool and 'research/llm-wiki' in item.skills"
        in profiles_tasks
    )


def test_profile_config_template_renders_scoped_mcp_only() -> None:
    env = _jinja_env()
    src = (ROLE_ROOT / "templates" / "config-profile.yaml.j2").read_text()
    defaults = _defaults()
    for profile in _profiles():
        context = dict(_RENDER_CONTEXT)
        context["hermes_agent_disabled_toolsets"] = defaults["hermes_agent_disabled_toolsets"]
        rendered = env.from_string(src).render(hermes_agent_profile=profile, **context)
        parsed = yaml.safe_load(rendered)

        assert parsed["kanban"] == {"dispatch_in_gateway": False}
        assert "dashboard" not in parsed
        assert "platforms" not in parsed
        assert "platform_toolsets" not in parsed
        # Goal-mode judging must be wired identically to the default profile,
        # or completion judging fails for any card this profile owns.
        assert "auxiliary" in parsed

        rendered_servers = set(parsed.get("mcp_servers", {}))
        assert rendered_servers == set(profile["mcp"])


def test_profile_env_template_blanks_every_ungranted_credential() -> None:
    env = _jinja_env()
    src = (ROLE_ROOT / "templates" / "hermes-env-profile.j2").read_text()
    context = dict(_RENDER_CONTEXT)
    context.update(
        hermes_agent_model_api_key="MODELKEY",
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
    )
    always_blank = ("GH_PAT_WRITE_PROJECT_ISSUES", "GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "CONTEXT7_API_KEY")
    section_keys = {
        "slack": ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"),
        "splunk": ("SPLUNK_MCP_URL", "SPLUNK_MCP_TOKEN"),
        "zammad": ("ZAMMAD_URL", "ZAMMAD_API_TOKEN"),
    }

    for profile in _profiles():
        rendered = env.from_string(src).render(hermes_agent_profile=profile, **context)
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
