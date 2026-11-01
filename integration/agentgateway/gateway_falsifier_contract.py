"""Pinned fixture inputs shared by the gateway acceptance test pieces."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = Path(__file__).with_name("compose.yml")
TESTBOX_SOURCE_COMMIT = "ea5608642b9d5c94c5baf5fd9f2f9849b808e963"
TESTBOX_IMAGE = "gateway-falsifier/testbox:0.0.1"
ENVOY_IMAGE = (
    "envoyproxy/envoy:v1.40.1@sha256:"
    "713bb146f1a7c97ebc70e13d259f77ff36f2c5d00f3145677816af5af9bd1530"
)
GATEWAY_URL = "http://127.0.0.1:15000"
OBSERVER_URL = "http://127.0.0.1:18002"
SIDECAR_OBSERVER_URL = "http://127.0.0.1:18003"
OWNER = "falsifier-owner"
NONOWNER = "falsifier-nonowner"
OWNER_KEY = "fixture-owner-key-not-a-secret"
NONOWNER_KEY = "fixture-nonowner-key-not-a-secret"
BAD_KEY = "fixture-invalid-key-not-a-secret"
TOY_BACKEND_AUTH = "fixture-toy-backend-auth-not-a-secret"
PROTOCOL_VERSION = "2025-03-26"
CALLER_CREDENTIAL_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "x-agent-key",
}
VIKUNJA_CALLER_MARKERS = {
    "optional_initialize": "vikunja-optional-no-key-initialize",
    "optional_initialized": "vikunja-optional-no-key-initialized",
    "optional_tools_list": "vikunja-optional-no-key-tools-list",
    "initialize": "vikunja-same-request-initialize",
    "initialized": "vikunja-same-request-initialized",
    "tools_list": "vikunja-same-request-tools-list",
    "task_read": "vikunja-same-request-task-read",
}
SUCCESS_URL = "https://fixture.invalid/falsifier-success"
ERROR_URL = ""
