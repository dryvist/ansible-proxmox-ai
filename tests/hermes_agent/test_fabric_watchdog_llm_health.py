"""The Hermes watchdog uses LiteLLM's unauthenticated liveness endpoint."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles" / "fabric_watchdog" / "defaults" / "main.yml").read_text()
)

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
