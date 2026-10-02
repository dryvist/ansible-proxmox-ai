"""Self-check for the event relay's pure logic (files/hermes-event-relay.py)."""
import base64
import hashlib
import hmac
import json
import runpy
import types
from pathlib import Path

import pytest

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
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


def test_parse_is_byte_exact():
    assert MOD.parse_envelope(envelope()) == ("pull_request", "d-1", RAW)


@pytest.mark.parametrize(
    "body",
    ["not json", "[]", envelope(event=""), envelope(delivery=None), envelope(body_b64="!!notb64"), "{}"],
)
def test_parse_rejects_malformed(body):
    with pytest.raises(ValueError):
        MOD.parse_envelope(body)


def test_target_url_by_visibility():
    assert MOD.target_url(b'{"repository":{"private":true}}', CFG).endswith("/webhooks/github-private")
    assert MOD.target_url(RAW, CFG) == "http://127.0.0.1:1/webhooks/github-public"
    for body in (b'{}', b'{"repository":{"private":"yes"}}', b'not json', b'[]'):
        with pytest.raises(ValueError):
            MOD.target_url(body, CFG)


def test_sign_matches_github_format():
    want = "sha256=" + hmac.new(b"s3cret", RAW, hashlib.sha256).hexdigest()
    assert MOD.sign("s3cret", RAW) == want


@pytest.mark.parametrize("status,delete", [(200, True), (202, True), (204, True), (401, False),
                                           (429, False), (500, False), (None, False)])
def test_delete_decision(status, delete):
    assert MOD.should_delete(status) is delete


def test_handle_forwards_and_deletes_on_2xx(capsys):
    seen = []

    def post(url, secret, event, delivery, raw):
        seen.append((url, secret, event, delivery, raw))
        return 200, '{"status": "duplicate"}'

    assert MOD.handle({"MessageId": "m", "Body": envelope()}, CFG, post=post) is True
    assert seen == [("http://127.0.0.1:1/webhooks/github-public", "s3cret", "pull_request", "d-1", RAW)]
    assert json.loads(capsys.readouterr().out)["msg"] == "relayed"


def test_handle_keeps_on_error_and_poison(capsys):
    called = []

    def busy(url, secret, event, delivery, raw):
        called.append(delivery)
        return 503, "busy"

    assert MOD.handle({"Body": envelope()}, CFG, post=busy) is False
    assert MOD.handle({"Body": "garbage"}, CFG, post=busy) is False
    assert called == ["d-1"]
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [x["msg"] for x in lines] == ["relayed", "poison"]
