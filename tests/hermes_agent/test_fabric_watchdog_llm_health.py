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
        {
            **DEFAULTS,
            "fabric_watchdog_hindsight_enabled": False,
            "ansible_managed": "test render",
        },
    )
    target_names = re.findall(
        r"^# --- ([a-z][a-z0-9-]*) -{3,}$", rendered, re.MULTILINE
    )

    assert set(target_names) == {
        "mcp-fabric",
        "llm-front-door",
    }
    assert all(
        name in {"mcp-fabric", "llm-front-door"}
        for name in target_names
    )


def test_hindsight_targets_return_only_when_the_service_is_enabled() -> None:
    template = template_text(ROLE, "fabric-watchdog.sh.j2")
    enabled = render(
        template,
        {**DEFAULTS, "fabric_watchdog_hindsight_enabled": True, "ansible_managed": "test render"},
    )
    disabled = render(
        template,
        {**DEFAULTS, "fabric_watchdog_hindsight_enabled": False, "ansible_managed": "test render"},
    )
    def target_names(text: str) -> set[str]:
        return set(re.findall(r"^# --- ([a-z][a-z0-9-]*) -{3,}$", text, re.MULTILINE))

    assert target_names(enabled) - target_names(disabled) == {"hindsight", "hindsight-dep-gate"}
    assert {"mcp-fabric", "llm-front-door"} <= target_names(disabled)
