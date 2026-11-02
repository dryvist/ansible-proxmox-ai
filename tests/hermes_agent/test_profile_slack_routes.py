"""Named profiles deliver Slack cron output through routed report channels.

A named profile holds no Slack token, so Hermes cron preflight refuses its
Slack delivery unless the primary gateway's `profile_routes` routes a channel
to that profile. Each configured profile channel must render one route, and a
named profile's single Slack target must move to its routed channel.
"""

from __future__ import annotations

import json
import re

import yaml
from jinja2 import Environment

from _role_files import role_defaults
from test_a2a_config import ROLE_ROOT, _gateway_fragment

CHANNELS = {"splunk-admin": "C0EXAMPLE1", "homelab-admin": "C0EXAMPLE2"}
BASE = {
    "hermes_agent_api_server_key": "",
    "hermes_agent_a2a_inbound_enabled": False,
    "hermes_agent_a2a_inbound_port": 9901,
    "hermes_agent_api_max_concurrent_runs": 8,
}


def test_each_profile_channel_renders_one_slack_route() -> None:
    gateway = yaml.safe_load(_gateway_fragment(**BASE, hermes_agent_slack_profile_channels=CHANNELS))["gateway"]
    assert gateway["multiplex_profiles"] is True
    routes = {r["profile"]: r for r in gateway["profile_routes"]}
    assert set(routes) == set(CHANNELS)
    for profile, channel in CHANNELS.items():
        assert routes[profile]["platform"] == "slack"
        assert routes[profile]["chat_id"] == channel


def test_no_profile_channels_renders_no_gateway_block() -> None:
    assert _gateway_fragment(**BASE, hermes_agent_slack_profile_channels={}) == ""


def _resolve_deliver(item: dict, channels: dict | None = None, digest_channel: str = "C0HOME") -> str:
    """Evaluate the reconcile task's deliver expressions as Ansible would."""
    task = next(
        t for t in yaml.safe_load((ROLE_ROOT / "tasks" / "reconcile_direct_cron.yml").read_text())
        if t["name"].startswith("Direct cron | Resolve deliver target")
    )
    env = Environment()
    env.tests["match"] = lambda v, p: re.match(p, str(v)) is not None
    ctx = {
        "item": item,
        "hermes_agent_digest_slack_channel": digest_channel,
        "hermes_agent_slack_profile_channels": channels or CHANNELS,
    }
    for key in ("_deliver", "_home", "_profile"):
        ctx[key] = env.from_string(task["vars"][key]).render(**ctx).strip()
    return env.from_string(task["ansible.builtin.set_fact"]["hermes_agent_direct_deliver"]).render(**ctx).strip()


def test_named_profile_slack_target_moves_to_its_channel() -> None:
    item = {"deliver": "slack:C0HOME", "hermes_home": "/h/profiles/splunk-admin"}
    assert _resolve_deliver(item) == "slack:" + CHANNELS["splunk-admin"]


def test_unrouted_or_default_targets_are_unchanged() -> None:
    assert _resolve_deliver({"deliver": "slack:C0HOME", "hermes_home": "/h"}) == "slack:C0HOME"
    assert _resolve_deliver({"deliver": "slack:C0HOME", "hermes_home": "/h/profiles/github-maint"}) == "slack:C0HOME"
    assert _resolve_deliver({"deliver": "local", "hermes_home": "/h/profiles/splunk-admin"}) == "local"
    assert (
        _resolve_deliver({"deliver": "slack:C0HOME,local", "hermes_home": "/h/profiles/splunk-admin"})
        == "slack:C0HOME,local"
    )


def _render_nested(value: str, context: dict) -> str:
    rendered = value
    for _ in range(3):
        next_value = Environment().from_string(str(rendered)).render(**context)
        if next_value == rendered:
            break
        rendered = next_value
    return rendered.strip()


def _declared_direct_jobs() -> list[dict]:
    jobs = []
    for path in sorted((ROLE_ROOT / "defaults" / "main").glob("*.yml")):
        for key, value in (yaml.safe_load(path.read_text()) or {}).items():
            if key.startswith("hermes_agent_direct_cron_jobs_") and isinstance(value, list):
                jobs.extend(value)
    return jobs


def test_every_routed_profile_direct_cron_single_slack_target_uses_its_channel() -> None:
    defaults = role_defaults(ROLE_ROOT)
    jobs = _declared_direct_jobs()
    profiles = {profile["name"] for profile in defaults["hermes_agent_profiles"]}
    profiles.add(defaults.get("hermes_agent_kanban_orchestrator_profile", "orchestrator"))
    profiles.update(
        profile
        for job in jobs
        for profile in re.findall(r"/profiles/([a-z0-9-]+)", str(job.get("hermes_home", "")))
    )
    channels = {profile: f"C0TEST{index:02d}" for index, profile in enumerate(sorted(profiles), 1)}
    context = {
        "hermes_agent_hermes_home": "/srv/hermes-home",
        "hermes_agent_kanban_orchestrator_profile": defaults.get(
            "hermes_agent_kanban_orchestrator_profile", "orchestrator"
        ),
        **{
            key: "C0TEST00"
            for key in defaults
            if key.startswith("hermes_agent_slack_") and key != "hermes_agent_slack_profile_channels"
        },
        "hermes_agent_digest_slack_channel": "C0TEST00",
        "hermes_agent_slack_profile_channels": channels,
    }
    checked = 0
    for job in jobs:
        raw_home = job.get("hermes_home")
        if not raw_home:
            continue
        home = _render_nested(raw_home, context)
        match = re.search(r"/profiles/([^/]+)$", home)
        if not match or match.group(1) not in channels:
            continue
        profile = match.group(1)
        expanded = dict(job)
        if "deliver" in expanded:
            expanded["deliver"] = _render_nested(expanded["deliver"], {**context, "item": job})
        expanded["hermes_home"] = home
        target = expanded.get("deliver", "slack:C0TEST00")
        if re.fullmatch(r"slack:[^,]+", target):
            assert _resolve_deliver(expanded, channels, "C0TEST00") == f"slack:{channels[profile]}", job["name"]
            checked += 1
    assert checked > 0


def test_enqueuer_items_do_not_set_profile_homes() -> None:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "cron_reconcile.yml").read_text())
    enqueuers = [
        task for task in tasks
        if task.get("ansible.builtin.include_tasks") == "reconcile_enqueuer_cron.yml"
    ]
    assert enqueuers
    for task in enqueuers:
        item = task.get("vars", {}).get("item", {})
        assert "hermes_home" not in item or "/profiles/" not in str(item["hermes_home"]), task["name"]


def _channels(env: dict, ai_agents: dict | None = None) -> str:
    """Render the profile-channel default as Ansible would, env lookups stubbed."""
    expr = role_defaults(ROLE_ROOT)["hermes_agent_slack_profile_channels"]
    jenv = Environment()
    jenv.filters["from_json"] = json.loads
    jenv.globals["lookup"] = lambda kind, name: env.get(name, "")
    return jenv.from_string(expr).render(
        ai_agents=ai_agents or {}, hermes_agent_id="hermes", hermes_agent_env_prefix="HERMES"
    ).strip()


def test_profile_channels_fall_back_to_the_run_environment() -> None:
    env = {"SLACK_HERMES_PROFILE_CHANNELS": json.dumps({"splunk-admin": CHANNELS["splunk-admin"]})}
    assert _channels(env) == str({"splunk-admin": CHANNELS["splunk-admin"]})
    roster = {"hermes": {"slack": {"profile_channels": CHANNELS}}}
    assert _channels(env, roster) == str(CHANNELS)
    assert _channels({}) == "{}"


def test_zammad_review_is_bounded_and_gated_on_its_route() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert "at most 5 tickets this run" in defaults["hermes_agent_zammad_review_cron_prompt"]
    job = next(
        j for j in defaults["hermes_agent_direct_cron_jobs"]
        if j["name"] == "{{ hermes_agent_zammad_review_cron_name }}"
    )
    assert "'homelab-admin' in hermes_agent_slack_profile_channels" in str(job["enabled"])
