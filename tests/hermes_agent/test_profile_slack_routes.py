"""Named profiles deliver Slack cron output through routed report channels.

A named profile holds no Slack token, so Hermes cron preflight refuses its
Slack delivery unless the primary gateway's `profile_routes` routes a channel
to that profile. Each configured profile channel must render one route, and a
named profile's single Slack target must move to its routed channel.
"""

from __future__ import annotations

import ast
import json
import re

import yaml
from jinja2 import Environment

from _role_files import include_file, role_defaults
from test_a2a_config import ROLE_ROOT, _gateway_fragment

CHANNELS = {"splunk-admin": "C0EXAMPLE1", "homelab-admin": "C0EXAMPLE2"}
BASE = {
    "hermes_agent_api_server_key": "",
    "hermes_agent_a2a_inbound_enabled": False,
    "hermes_agent_a2a_inbound_port": 9901,
    "hermes_agent_api_max_concurrent_runs": 8,
}

# One string value and one list value: the two report-channel forms side by side.
MULTI_CHANNELS = {"a": "C0EXAMPLE1", "b": ["C0EXAMPLE2", "C0EXAMPLE3"]}


def test_each_profile_channel_renders_one_slack_route() -> None:
    gateway = yaml.safe_load(_gateway_fragment(**BASE, hermes_agent_slack_profile_channels=CHANNELS))["gateway"]
    assert gateway["multiplex_profiles"] is True
    routes = {r["profile"]: r for r in gateway["profile_routes"]}
    assert set(routes) == set(CHANNELS)
    for profile, channel in CHANNELS.items():
        assert routes[profile]["platform"] == "slack"
        assert routes[profile]["chat_id"] == channel


def test_each_report_channel_renders_one_slack_route() -> None:
    """A list value renders one route per channel: <profile>-reports for the
    first channel and <profile>-reports-<n> for the n-th further channel,
    n starting at 2."""
    gateway = yaml.safe_load(
        _gateway_fragment(**BASE, hermes_agent_slack_profile_channels=MULTI_CHANNELS)
    )["gateway"]
    assert gateway["multiplex_profiles"] is True
    assert [(r["name"], r["platform"], r["chat_id"], r["profile"]) for r in gateway["profile_routes"]] == [
        ("a-reports", "slack", "C0EXAMPLE1", "a"),
        ("b-reports", "slack", "C0EXAMPLE2", "b"),
        ("b-reports-2", "slack", "C0EXAMPLE3", "b"),
    ]


def test_no_profile_channels_renders_no_gateway_block() -> None:
    assert _gateway_fragment(**BASE, hermes_agent_slack_profile_channels={}) == ""


def _profile_deliver_targets(channels: dict) -> dict[str, str]:
    expr = role_defaults(ROLE_ROOT)["hermes_agent_slack_profile_deliver_targets"]
    env = Environment()
    env.filters["regex_replace"] = lambda value, pattern, replacement="": re.sub(
        pattern, replacement, value
    )
    env.filters["zip"] = lambda left, right: zip(left, right)
    rendered = env.from_string(str(expr)).render(hermes_agent_slack_profile_channels=channels)
    return ast.literal_eval(rendered)


def test_profile_deliver_targets_add_the_slack_prefix_from_the_route_map() -> None:
    assert _profile_deliver_targets(CHANNELS) == {
        profile: f"slack:{channel}" for profile, channel in CHANNELS.items()
    }
    assert _profile_deliver_targets({}) == {}


def test_profile_deliver_targets_are_the_first_report_channel() -> None:
    assert _profile_deliver_targets(MULTI_CHANNELS) == {
        "a": "slack:C0EXAMPLE1",
        "b": "slack:C0EXAMPLE2",
    }


def _resolve_deliver(item: dict, channels: dict | None = None, digest_channel: str = "C0HOME") -> str:
    """Evaluate the reconcile task's deliver expressions as Ansible would."""
    task = next(
        t for t in yaml.safe_load((ROLE_ROOT / "tasks" / "reconcile_direct_cron_plan.yml").read_text())
        if t["name"] == "Direct cron | Plan every job"
    )
    env = Environment()
    env.tests["match"] = lambda v, p: re.match(p, str(v)) is not None
    env.filters["regex_replace"] = lambda value, pattern, replacement="": re.sub(pattern, replacement, value)
    ctx = {
        "hermes_agent_cron_dm_pattern": role_defaults(ROLE_ROOT)["hermes_agent_cron_dm_pattern"],
        "hermes_agent_cron_dm_replacement": r"\1local",
        "item": item,
        "hermes_agent_digest_slack_channel": digest_channel,
        "hermes_agent_slack_profile_channels": channels or CHANNELS,
        "hermes_agent_slack_profile_deliver_targets": _profile_deliver_targets(channels or CHANNELS),
    }
    for key in ("_deliver", "_home", "_profile"):
        ctx[key] = env.from_string(task["vars"][key]).render(**ctx).strip()
    return env.from_string(task["vars"]["hermes_agent_direct_deliver"]).render(**ctx).strip()


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


def test_resolve_keeps_a_target_that_is_already_one_of_the_channels() -> None:
    assert _resolve_deliver(
        {"deliver": "slack:C0EXAMPLE3", "hermes_home": "/h/profiles/b"}, channels=MULTI_CHANNELS
    ) == "slack:C0EXAMPLE3"
    assert _resolve_deliver(
        {"deliver": "slack:C0HOME", "hermes_home": "/h/profiles/b"}, channels=MULTI_CHANNELS
    ) == "slack:C0EXAMPLE2"


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


def test_every_profile_homed_slack_cron_uses_its_gateway_route() -> None:
    defaults = role_defaults(ROLE_ROOT)
    jobs = _declared_direct_jobs()
    channels = {
        **CHANNELS, "github-maint": "C0EXAMPLE3", "orchestrator": "C0EXAMPLE4",
        "tracker-reporter": ["C0EXAMPLE5", "C0EXAMPLE6"],
    }
    context = {
        "hermes_agent_tracker_report_channels": channels["tracker-reporter"],
        "hermes_agent_hermes_home": "/srv/hermes-home",
        "hermes_agent_kanban_orchestrator_profile": defaults.get(
            "hermes_agent_kanban_orchestrator_profile", "orchestrator"
        ),
        "hermes_agent_credential_canary_profile": defaults[
            "hermes_agent_credential_canary_profile"
        ],
        **{
            key: "C0TEST00"
            for key in defaults
            if key.startswith("hermes_agent_slack_") and key != "hermes_agent_slack_profile_channels"
        },
        "hermes_agent_digest_slack_channel": "C0TEST00",
        "hermes_agent_slack_profile_channels": channels,
        "hermes_agent_slack_profile_deliver_targets": _profile_deliver_targets(channels),
    }
    checked = 0
    for job in jobs:
        raw_home = job.get("hermes_home")
        if not raw_home:
            continue
        home = _render_nested(raw_home, context)
        match = re.search(r"/profiles/([^/]+)$", home)
        if not match:
            continue
        profile = match.group(1)
        expanded = dict(job)
        if "deliver" in expanded:
            expanded["deliver"] = _render_nested(expanded["deliver"], {**context, "item": job})
        expanded["hermes_home"] = home
        target = expanded.get("deliver", "slack:C0TEST00")
        if str(target).startswith("slack:"):
            assert re.fullmatch(r"slack:[^,]+", str(target)), job["name"]
            assert profile in channels, f"{job['name']} has no gateway Slack route for {profile!r}"
            routed = channels[profile] if isinstance(channels[profile], list) else [channels[profile]]
            assert _resolve_deliver(expanded, channels, "C0TEST00") in {
                f"slack:{channel}" for channel in routed
            }, job["name"]
            raw_deliver = str(job.get("deliver", ""))
            if (
                "hermes_agent_slack_profile_deliver_targets" in raw_deliver
                and not job.get("no_agent", False)
                and job.get("enabled") is not False
            ):
                route_key = (
                    "hermes_agent_kanban_orchestrator_profile"
                    if "hermes_agent_kanban_orchestrator_profile" in raw_deliver
                    else repr(profile)
                )
                assert f"{route_key} in hermes_agent_slack_profile_deliver_targets" in str(
                    job.get("enabled", "")
                ), job["name"]
            checked += 1
    assert checked > 0


def test_enqueuer_items_do_not_set_profile_homes() -> None:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "cron_reconcile.yml").read_text())
    enqueuers = [
        task for task in tasks
        if include_file(task) == "reconcile_enqueuer_cron.yml"
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


def test_zammad_review_requires_its_reader_credentials_and_gateway_route() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert "at most 5 tickets this run" in defaults["hermes_agent_zammad_review_cron_prompt"]
    job = next(
        j for j in defaults["hermes_agent_direct_cron_jobs"]
        if j["name"] == "{{ hermes_agent_zammad_review_cron_name }}"
    )
    reader = next(
        profile for profile in defaults["hermes_agent_profiles"]
        if profile["name"] == "homelab-admin"
    )
    assert job["skill"] == "dryvist/zammad-incidents"
    assert job["hermes_home"].endswith("/profiles/homelab-admin")
    assert "zammad" in reader["env"]
    assert job["skill"] in reader["skills"]
    assert job["deliver"] == (
        "{{ hermes_agent_slack_profile_deliver_targets.get('homelab-admin', '') }}"
    )

    enabled = Environment().from_string(str(job["enabled"]))
    ready = {
        "hermes_agent_zammad_url": "https://zammad.example.com",
        "hermes_agent_zammad_api_token": "fixture-token",
        "hermes_agent_slack_bot_token": "fixture-bot-token",
        "hermes_agent_slack_app_token": "fixture-app-token",
        "hermes_agent_slack_profile_deliver_targets": {"homelab-admin": "slack:C0EXAMPLE2"},
    }
    assert enabled.render(**ready).strip() == "True"
    for missing in (
        "hermes_agent_zammad_url",
        "hermes_agent_zammad_api_token",
        "hermes_agent_slack_bot_token",
        "hermes_agent_slack_app_token",
    ):
        unavailable = {**ready, missing: ""}
        assert enabled.render(**unavailable).strip() == "False", missing
    assert enabled.render(
        **{**ready, "hermes_agent_slack_profile_deliver_targets": {}}
    ).strip() == "False"
    assert enabled.render(
        **{
            **ready,
            "hermes_agent_slack_profile_deliver_targets": {"homelab-admin": "slack:"},
        }
    ).strip() == "False"


PROFILE_ROUTE_FLEET = ("splunk-admin", "homelab-admin")


def _two_profile_jobs() -> list[tuple[dict, str]]:
    found = []
    for job in _declared_direct_jobs():
        match = re.search(r"/profiles/([a-z0-9-]+)", str(job.get("hermes_home", "")))
        if match and match.group(1) in PROFILE_ROUTE_FLEET:
            found.append((job, match.group(1)))
    assert found, "the splunk-admin/homelab-admin cron fleet went missing"
    return found


def test_two_profile_fleet_delivers_only_through_the_route_map() -> None:
    """Every splunk-admin/homelab-admin job delivers to its profile's routed
    channel on the primary gateway — the profile holds no Slack token (one
    Slack socket per identity), so a deliver expression naming a literal
    shared channel or an active gate without the route-membership check would
    enqueue output nothing can send.
    """
    for job, profile in _two_profile_jobs():
        if "deliver" in job:
            assert "hermes_agent_slack_profile_deliver_targets" in str(job["deliver"]), job["name"]
        enabled = job.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            assert f"'{profile}' in hermes_agent_slack_profile_deliver_targets" in str(enabled), (
                job["name"]
            )
