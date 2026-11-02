"""Named profiles deliver Slack cron output through routed report channels.

A named profile holds no Slack token, so Hermes cron preflight refuses its
Slack delivery unless the primary gateway's `profile_routes` routes a channel
to that profile. Each configured profile channel must render one route, and a
named profile's single Slack target must move to its routed channel.
"""

from __future__ import annotations

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


def _resolve_deliver(item: dict) -> str:
    """Evaluate the reconcile task's deliver expressions as Ansible would."""
    task = next(
        t for t in yaml.safe_load((ROLE_ROOT / "tasks" / "reconcile_direct_cron.yml").read_text())
        if t["name"].startswith("Direct cron | Resolve deliver target")
    )
    env = Environment()
    env.tests["match"] = lambda v, p: re.match(p, str(v)) is not None
    ctx = {
        "item": item,
        "hermes_agent_digest_slack_channel": "C0HOME",
        "hermes_agent_slack_profile_channels": CHANNELS,
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


def test_zammad_review_is_bounded_and_gated_on_its_route() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert "at most 5 tickets this run" in defaults["hermes_agent_zammad_review_cron_prompt"]
    job = next(
        j for j in defaults["hermes_agent_direct_cron_jobs"]
        if j["name"] == "{{ hermes_agent_zammad_review_cron_name }}"
    )
    assert "'homelab-admin' in hermes_agent_slack_profile_channels" in str(job["enabled"])
