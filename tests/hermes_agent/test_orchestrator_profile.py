from __future__ import annotations

import yaml

from test_profile_contract import (
    ROLE_ROOT,
    _RENDER_CONTEXT,
    _defaults,
    _jinja_env,
    _profiles,
)


def test_orchestrator_is_the_only_profile_with_vikunja_and_has_a_bounded_soul() -> None:
    defaults = _defaults()
    profiles = _profiles()
    orchestrator = next(profile for profile in profiles if profile["name"] == "orchestrator")

    assert orchestrator["description"] == (
        "Kanban decomposition and assignment owner; the only profile that reads the Vikunja backlog"
    )
    assert orchestrator["mcp"] == ["vikunja", "docs"]
    assert orchestrator["env"] == []
    assert orchestrator["skills"] == ["hermes_agent/github-issues-api"]
    assert all("vikunja" not in profile["mcp"] for profile in profiles if profile is not orchestrator)
    assert defaults["hermes_agent_kanban_orchestrator_profile"] == "orchestrator"
    assert defaults["hermes_agent_kanban_eligible_label"] == "hermes-eligible"

    soul = ROLE_ROOT / "templates" / orchestrator["soul_addendum_file"]
    rendered = _jinja_env().from_string(soul.read_text()).render(
        hermes_agent_kanban_eligible_label=defaults["hermes_agent_kanban_eligible_label"]
    )
    assert len(rendered.encode("ascii")) <= 150
    assert "Only pull Vikunja tasks labelled `hermes-eligible`" in rendered
    assert "best-fit profile by description" in rendered
    assert "Never work cards" in rendered


def test_profile_toolsets_are_scoped_and_orchestrator_has_kanban() -> None:
    defaults = _defaults()
    env = _jinja_env()
    template = env.get_template("config-profile.yaml.j2")
    for profile in _profiles():
        context = dict(_RENDER_CONTEXT)
        context.update(
            hermes_agent_profile=profile,
            hermes_agent_context_limits={},
            hermes_agent_disabled_toolsets=defaults["hermes_agent_disabled_toolsets"],
            hermes_agent_cron_toolsets=defaults["hermes_agent_api_server_toolsets"],
        )
        config = yaml.safe_load(template.render(**context))
        expected = {
            platform: (
                defaults["hermes_agent_api_server_toolsets"] + additions
                if platform == "cron" else additions
            )
            for platform, additions in profile.get("toolsets", {}).items()
        }
        assert config.get("platform_toolsets", {}) == expected
        if profile["name"] != "orchestrator":
            assert "kanban" not in profile.get("toolsets", {}).get("cron", [])

    orchestrator_config = yaml.safe_load(template.render(
        **{
            **_RENDER_CONTEXT,
            "hermes_agent_profile": next(p for p in _profiles() if p["name"] == "orchestrator"),
            "hermes_agent_context_limits": {},
            "hermes_agent_disabled_toolsets": defaults["hermes_agent_disabled_toolsets"],
            "hermes_agent_cron_toolsets": defaults["hermes_agent_api_server_toolsets"],
        }
    ))
    assert "kanban" in orchestrator_config["platform_toolsets"]["cli"]
    assert "kanban" in orchestrator_config["platform_toolsets"]["cron"]
    assert set(orchestrator_config["mcp_servers"]) == {"vikunja", "docs"}


def test_card_crons_get_kanban_only_through_their_per_job_toolsets() -> None:
    defaults = _defaults()
    env = _jinja_env()
    cron = yaml.safe_load(env.from_string(
        str(defaults["hermes_agent_cron_toolsets"])
    ).render(hermes_agent_api_server_toolsets=defaults["hermes_agent_api_server_toolsets"]))
    slack = yaml.safe_load(env.from_string(
        str(defaults["hermes_agent_slack_toolsets"])
    ).render(hermes_agent_cron_toolsets=cron, hermes_agent_a2a_peers=[]))
    assert "kanban" not in cron and "kanban" not in slack

    profiles = {profile["name"]: profile for profile in _profiles()}
    card_jobs = [
        job for job in defaults["hermes_agent_direct_cron_jobs"]
        if job.get("prompt_var") and "kanban_create" in str(defaults.get(job["prompt_var"], ""))
    ]
    assert card_jobs
    for job in card_jobs:
        assert "hermes_home" in job, job["name"]
        assert "enabled_toolsets" in job, job["name"]
        job_toolsets = yaml.safe_load(env.from_string(str(job["enabled_toolsets"])).render(
            hermes_agent_cron_toolsets=defaults["hermes_agent_api_server_toolsets"]
        ))
        assert "kanban" in job_toolsets, job["name"]
        assert set(defaults["hermes_agent_api_server_toolsets"]).issubset(job_toolsets)
        home = env.from_string(job["hermes_home"]).render(
            hermes_agent_hermes_home="/home/hermes/.hermes",
            hermes_agent_kanban_orchestrator_profile="orchestrator",
        )
        profile_name = home.rsplit("/", 1)[-1]
        assert profile_name in profiles

    reconciler = (ROLE_ROOT / "tasks" / "reconcile_direct_cron.yml").read_text()
    assert "item.hermes_home | default(hermes_agent_hermes_home)" in reconciler
    assert "from tools.cronjob_tools import cronjob" in reconciler
    assert "enabled_toolsets: \"{{ item.enabled_toolsets | default([]) }}\"" in reconciler
