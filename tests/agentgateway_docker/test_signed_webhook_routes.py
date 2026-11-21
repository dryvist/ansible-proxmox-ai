"""Signed webhook relays: /hooks/<name> forwards to the backend and renames the signature header."""

from __future__ import annotations

from test_authz_render import ROLE_DEFAULTS, _render, _routes

HOOK = {
    "name": "task-event",
    "host": "agent.example.test:8644",
    "backend_path": "/webhooks/task-event",
    "signature_header": "x-sender-signature",
}


def _hook_routes(hooks: list[dict[str, str]]) -> list[dict]:
    routes = _routes(_render(extra={"agentgateway_docker_signed_webhook_routes": hooks}))
    return [r for r in routes if r["matches"][0]["path"].get("exact", "").startswith("/hooks/")]


def test_no_relays_by_default() -> None:
    assert ROLE_DEFAULTS["agentgateway_docker_signed_webhook_routes"] == []
    assert _hook_routes([]) == []


def test_relay_rewrites_the_path_and_renames_the_signature_header() -> None:
    [route] = _hook_routes([HOOK])
    assert route == {
        "matches": [{"path": {"exact": "/hooks/task-event"}}],
        "policies": {
            "urlRewrite": {"path": {"full": "/webhooks/task-event"}},
            "transformations": {"request": {
                "set": {"linear-signature": 'request.headers["x-sender-signature"]'},
                "remove": ["x-sender-signature"],
            }},
        },
        "backends": [{"host": "agent.example.test:8644"}],
    }


def test_verified_header_can_be_overridden() -> None:
    [route] = _hook_routes([HOOK | {"verified_header": "x-webhook-signature"}])
    assert route["policies"]["transformations"]["request"]["set"] == {
        "x-webhook-signature": 'request.headers["x-sender-signature"]'
    }
