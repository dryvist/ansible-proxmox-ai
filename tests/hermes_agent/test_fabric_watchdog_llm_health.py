"""The Hermes watchdog uses LiteLLM's unauthenticated liveness endpoint."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from _role_files import template_text
from _shell_harness_shared import ROLES, render

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles" / "fabric_watchdog" / "defaults" / "main.yml").read_text()
)
ROLE = ROLES / "fabric_watchdog"

# Read-only live response from the router's health endpoint on 2026-10-07.
LIVE_LITELLM_HEALTH_RESPONSE = {"status_code": 200, "body": '"I\'m alive!"'}


def test_front_door_probe_accepts_the_live_unauthenticated_health_response() -> None:
    target = next(
        target
        for target in DEFAULTS["fabric_watchdog_targets"]
        if target["name"] == "llm-front-door"
    )

    assert target["url"].endswith("/health/liveliness")
    assert target["ok_codes"].split() == [str(LIVE_LITELLM_HEALTH_RESPONSE["status_code"])]


def test_rendered_script_keeps_only_the_front_door_llm_target() -> None:
    rendered = render(
        template_text(ROLE, "fabric-watchdog.sh.j2"),
        {**DEFAULTS, "ansible_managed": "test render"},
    )
    target_names = re.findall(
        r"^# --- ([a-z][a-z0-9-]*) -{3,}$", rendered, re.MULTILINE
    )

    assert set(target_names) == {
        "mcp-fabric",
        "llm-front-door",
        "hindsight",
        "hindsight-dep-gate",
    }
    assert all(
        name == "llm-front-door" or not name.startswith("llm-")
        for name in target_names
    )
