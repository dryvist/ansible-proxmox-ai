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


def test_public_events_use_the_public_base_and_private_the_default():
    cfg = {**CFG, "RELAY_TARGET_BASE": "http://127.0.0.1:8644/webhooks",
           "RELAY_TARGET_BASE_PUBLIC": "http://127.0.0.1:8645/webhooks/"}
    assert MOD.target_url(RAW, cfg) == "http://127.0.0.1:8645/webhooks/github-public"
    private = MOD.target_url(b'{"repository":{"private":true}}', cfg)
    assert private == "http://127.0.0.1:8644/webhooks/github-private"


def test_public_base_is_optional_and_still_loopback_checked():
    assert MOD.target_url(RAW, {**CFG, "RELAY_TARGET_BASE_PUBLIC": ""}) == "http://127.0.0.1:1/webhooks/github-public"
    remote = MOD.target_url(RAW, {**CFG, "RELAY_TARGET_BASE_PUBLIC": "http://gw.example.test/webhooks"})
    with pytest.raises(ValueError):
        MOD.forward(remote, "s3cret", "pull_request", "d-1", RAW)


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


# --- short-lived queue credentials -------------------------------------------

BAO_CFG = {"RELAY_BAO_ADDR": "https://bao.example.test/", "RELAY_BAO_ROLE_ID": "rid",
           "RELAY_BAO_SECRET_ID": "sid", "RELAY_BAO_CREDS_PATH": "aws/sts/example-role"}


class FakeBao:
    def __init__(self, lease=900, data=None):
        self.calls, self.lease, self.n = [], lease, 0
        self.data = data

    def __call__(self, url, body, token=None):
        self.calls.append((url, body, token))
        if url.endswith("/auth/approle/login"):
            return {"auth": {"client_token": "bao-tok"}}
        if url.endswith("/revoke-self"):
            return {}
        self.n += 1
        data = self.data if self.data is not None else {
            "access_key": f"AK{self.n}", "secret_key": "sk", "security_token": "st"}
        return {"lease_duration": self.lease, "data": data}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_credentials_refresh_before_expiry():
    bao, clock = FakeBao(lease=900), Clock()
    sts = MOD.StsCredentials(BAO_CFG, http=bao, clock=clock, margin=300)
    assert sts.needs_refresh()
    creds = sts.refresh()
    assert creds == {"aws_access_key_id": "AK1", "aws_secret_access_key": "sk", "aws_session_token": "st"}
    assert [c[0] for c in bao.calls] == [
        "https://bao.example.test/v1/auth/approle/login",
        "https://bao.example.test/v1/aws/sts/example-role",
        "https://bao.example.test/v1/auth/token/revoke-self",
    ]
    assert bao.calls[1][1] is None and bao.calls[1][2] == "bao-tok"
    clock.t += 599
    assert not sts.needs_refresh()
    clock.t += 1  # 600s in: 300s margin before the 900s lease ends
    assert sts.needs_refresh()
    assert sts.refresh()["aws_access_key_id"] == "AK2"


def test_credentials_reject_incomplete_or_short_lease():
    with pytest.raises(ValueError):
        MOD.StsCredentials(BAO_CFG, http=FakeBao(data={"access_key": "a", "secret_key": "s"})).refresh()
    with pytest.raises(ValueError):
        MOD.StsCredentials(BAO_CFG, http=FakeBao(lease=120), margin=300).refresh()


def test_failed_read_still_revokes():
    calls = []

    def http(url, body, token=None):
        calls.append(url)
        if url.endswith("/login"):
            return {"auth": {"client_token": "t"}}
        if url.endswith("/revoke-self"):
            return {}
        raise OSError("down")

    with pytest.raises(OSError):
        MOD.StsCredentials(BAO_CFG, http=http).refresh()
    assert calls[-1].endswith("/auth/token/revoke-self")


@pytest.mark.parametrize("url,ok", [
    ("https://bao.example.test/v1/x", True),
    ("http://127.0.0.1:8644/webhooks/github-public", True),
    ("http://localhost:1/x", True),
    ("http://bao.example.test/v1/x", False),
    ("file:///etc/passwd", False),
    ("ftp://example.test/", False),
])
def test_checked_url(url, ok):
    if ok:
        assert MOD.checked_url(url) == url
    else:
        with pytest.raises(ValueError):
            MOD.checked_url(url)
