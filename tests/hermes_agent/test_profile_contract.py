from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader
from _role_files import role_defaults, role_tasks_text, template_text


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
    "hermes_agent_kanban_goal_judge_model": "goal-judge",
    "hermes_agent_session_reset_at_hour": 4,
    "hermes_agent_session_reset_idle_minutes": 1440,
    "hermes_agent_mcp_tool_timeout_seconds": 180,
    "hermes_agent_docs_mcp_url": "https://mcp.example.com/docs",
    # grep.app is the one MCP client that dials upstream directly rather than a
    # gateway route, so the real URL is the fixture value too — there is no
    # per-estate hostname to stand in for.
    "hermes_agent_grep_mcp_url": "https://mcp.grep.app",
    "hermes_agent_vikunja_mcp_url": "https://mcp.example.com/vikunja",
    "hermes_agent_nautobot_mcp_url": "https://mcp.example.com/nautobot",
    "hermes_agent_splunk_mcp_enabled": True,
    "hermes_agent_splunk_mcp_url": "https://mcp.example.com/splunk",
    "hermes_agent_docs_mcp_enabled": True,
    "hermes_agent_grep_mcp_enabled": True,
    "hermes_agent_vikunja_mcp_enabled": False,
    "hermes_agent_nautobot_mcp_enabled": False,
    "hermes_agent_timezone": "UTC",
}


def _jinja_env() -> Environment:
    # A loader, so `{% include %}` in an env template resolves out of the
    # role's templates/ dir the way Ansible's own template action resolves it.
    env = Environment(autoescape=False, loader=FileSystemLoader(ROLE_ROOT / "templates"))
    env.filters["to_json"] = json.dumps
    env.filters["bool"] = bool
    env.filters["comment"] = lambda v: f"# {v}"
    # Passthrough: this module isn't testing router-key selection (that's
    # test_profile_router_key_selection.py), just credential blanking, so a
    # missing bao_apps_secrets field should render blank here, not raise.
    env.filters["mandatory"] = lambda v, msg="": v
    return env


def _defaults() -> dict[str, Any]:
    return role_defaults(ROLE_ROOT)


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


# _cards() and its three assignee/profile tests were removed: `assignee` no
# longer exists as a field anywhere (18/18 native-cron reframe) —
# `hermes cron create` has no profile-selection flag at all, so every
# converted job runs under the default profile regardless of what the retired
# hermes_agent_kanban_cards entry used to name. Flagged as a real gap in the
# PR (6 of the 18 cards, including every splunk-monitor one, had a non-default
# assignee), not silently dropped.


def test_profile_env_lists_never_grant_a_forbidden_credential_section() -> None:
    # Each profile's Must-NOT list, encoded as forbidden .env sections
    # (roles/hermes_agent/README.md "Operating profiles" table).
    forbidden_sections = {
        "splunk-admin": {"zammad", "github"},
        "homelab-admin": {"splunk", "github"},
        "github-maint": {"splunk", "zammad"},
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
        "github-maint": {"splunk", "codex", "context7", "vikunja", "nautobot"},
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
        # docs-pr is the SIGNED-COMMIT path — a read-only profile must never
        # carry it, and that is the one GitHub skill github-maint is denied.
        "github-maint": {"dryvist/splunk-monitor", "dryvist/zammad-incidents", "dryvist/docs-pr"},
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


def test_profiles_tasks_are_wired_before_the_direct_cron_reconcile() -> None:
    """Native-cron reframe (18/18): there is no Kanban card left whose
    `assignee` needs a profile to exist first — direct-cron jobs carry no
    assignee field at all (see the PR's gap report). Profiles still need to be
    wired before the direct-cron reconcile loop runs, since some of its
    prompts reference profile-scoped skills."""
    tasks = role_tasks_text(ROLE_ROOT)
    profiles_idx = tasks.index("Reconcile named Hermes operating profiles")
    direct_idx = tasks.index("Reconcile the agentic direct-deliver digest crons")
    assert profiles_idx < direct_idx


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


def test_github_maint_cron_runs_in_its_own_profile_behind_the_read_token() -> None:
    """The job's read-only property comes from WHERE it runs, not its prompt.

    Two things carry it: the job must point HERMES_HOME at the github-maint
    profile (whose .env holds the read-only token and blanks everything else),
    and it must stay disabled until that token is actually seeded. Drop either
    and the job silently becomes an ordinary default-profile job holding the
    read/write PAT — which is exactly what it exists not to be.
    """
    defaults = _defaults()
    jobs = {
        entry["name"]: entry for entry in defaults["hermes_agent_direct_cron_jobs"]
    }
    entry = jobs["{{ hermes_agent_github_maint_cron_name }}"]

    assert entry["hermes_home"].endswith("/profiles/github-maint")
    assert "hermes_agent_github_read_token | length > 0" in entry["enabled"]
    assert "hermes_agent_github_issues_pat" not in entry["enabled"]
    assert defaults["hermes_agent_github_read_token"] == ""

    # Least-shared tier: the read token belongs in one profile's .env, not in
    # the default profile's, which already holds the broader write PAT.
    default_env = template_text(ROLE_ROOT, "hermes-env.j2")
    assert "hermes_agent_github_read_token" not in default_env


def test_every_profile_cron_store_gets_its_own_tick_trigger() -> None:
    """A job on a named profile registers in that profile's own cron store,
    which only that profile's ticker drains — the default gateway's in-process
    ticker never sees it. The tick loop must therefore cover every profile, so
    it is derived from hermes_agent_profiles rather than hand-kept: a
    hardcoded list leaves a new profile's job created, scheduled, and silent.
    """
    tasks = role_tasks_text(ROLE_ROOT)
    assert (
        "loop: \"{{ hermes_agent_profiles | map(attribute='name') | list }}\"" in tasks
    ), "the profile cron tick trigger must loop over every profile, not a fixed list"


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


