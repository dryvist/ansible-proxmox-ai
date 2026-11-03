"""Shared fixtures for the event relay tests (files/hermes-event-relay.py)."""
import base64
import json
import runpy
import sys
import types
from pathlib import Path

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
# The relay imports its Dify route module from the same directory.
sys.path.insert(0, str(ROLE / "files"))
MOD = types.SimpleNamespace(**runpy.run_path(str(ROLE / "files" / "hermes-event-relay.py"), run_name="relay"))

RAW = b'{"action":"opened","repository":{"private":false}}\n'
CFG = {
    "RELAY_TARGET_BASE": "http://127.0.0.1:1/webhooks/",
    "RELAY_ROUTE_PUBLIC": "github-public",
    "RELAY_ROUTE_PRIVATE": "github-private",
    "RELAY_WEBHOOK_SECRET": "s3cret",
}


def envelope(**over):
    env = {"event": "pull_request", "delivery": "d-1", "headers": {}, "body_b64": base64.b64encode(RAW).decode()}
    env.update(over)
    return json.dumps(env)
