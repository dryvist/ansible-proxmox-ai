"""Self-check for the event relay's pure logic (files/hermes-event-relay.py)."""
import base64
import hashlib
import hmac
import json
import runpy
import subprocess
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


# --- Dify workflow routes -----------------------------------------------------

RAW_CHECK_FAILED = json.dumps({"action": "completed", "check_run": {"name": "ci", "conclusion": "failure"},
                               "repository": {"private": False, "full_name": "o/r"}}).encode()
RAW_CHECK_OK = json.dumps({"action": "completed", "check_run": {"name": "ci", "conclusion": "success"},
                           "repository": {"private": False, "full_name": "o/r"}}).encode()
RAW_MERGED = json.dumps({"action": "closed", "pull_request": {"merged": True, "number": 7},
                         "repository": {"private": False}}).encode()
RAW_CLOSED_UNMERGED = json.dumps({"action": "closed", "pull_request": {"merged": False, "number": 8},
                                  "repository": {"private": False}}).encode()
ROUTE_DEFS = [
    {"name": "ci-fixer", "base": "https://dify.example/", "app_key_env": "KEY_CI",
     "match": {"event": "check_run", "action": "completed", "where": {"check_run.conclusion": "failure"}},
     "inputs": {"repo": "repository.full_name", "check_name": "check_run.name"}},
    {"name": "docs-updater", "base": "https://dify.example", "app_key_env": "KEY_DOCS",
     "match": {"event": "pull_request", "action": "closed", "where": {"pull_request.merged": True}},
     "inputs": {"pr": "pull_request.number"}},
]


def active_routes(env):
    return MOD.load_routes(json.dumps(ROUTE_DEFS), env)


def test_match_route_by_event_action_and_where():
    routes = active_routes({"KEY_CI": "k1", "KEY_DOCS": "k2"})
    assert MOD.match_route("check_run", RAW_CHECK_FAILED, routes)["name"] == "ci-fixer"
    assert MOD.match_route("check_run", RAW_CHECK_OK, routes) is None
    assert MOD.match_route("pull_request", RAW_MERGED, routes)["name"] == "docs-updater"
    assert MOD.match_route("pull_request", RAW_CLOSED_UNMERGED, routes) is None
    assert MOD.match_route("push", RAW_MERGED, routes) is None
    assert MOD.match_route("check_run", RAW_CHECK_FAILED, []) is None


def test_load_routes_drops_route_without_app_key(capsys):
    routes = active_routes({"KEY_CI": "k1"})
    assert [r["name"] for r in routes] == ["ci-fixer"]
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [(x["msg"], x["route"]) for x in lines] == [("route_disabled", "docs-updater")]


def test_load_routes_bad_json_keeps_default_route(capsys):
    assert MOD.load_routes("{not json", {}) == []
    assert MOD.load_routes('{"name": "x"}', {}) == []
    assert "route_config_invalid" in capsys.readouterr().out


def test_dify_inputs_maps_dotted_paths_and_blanks_missing():
    mapping = {"repo": "repository.full_name", "check_name": "check_run.name", "missing": "pull_request.number"}
    assert MOD.dify_inputs("check_run", RAW_CHECK_FAILED, mapping) == {
        "event": "check_run", "repo": "o/r", "check_name": "ci", "missing": "",
    }


def test_handle_routes_matched_events_to_dify_and_others_to_default(capsys):
    routes = active_routes({"KEY_CI": "k1", "KEY_DOCS": "k2"})
    cfg = dict(CFG, ROUTES=routes)
    dify_calls, default_calls = [], []

    def dify(base, key, inputs, timeout):
        dify_calls.append((base, key, inputs, timeout))
        return 200, "run-1", ""

    def default_post(url, secret, event, delivery, raw):
        default_calls.append(url)
        return 200, "{}"

    failed = {"Body": envelope(event="check_run", body_b64=base64.b64encode(RAW_CHECK_FAILED).decode())}
    plain = {"Body": envelope()}
    assert MOD.handle(failed, cfg, post=default_post, dify_post=dify) is True
    assert MOD.handle(plain, cfg, post=default_post, dify_post=dify) is True
    assert dify_calls == [("https://dify.example/", "k1", {"event": "check_run", "repo": "o/r", "check_name": "ci"}, 30)]
    assert default_calls == ["http://127.0.0.1:1/webhooks/github-public"]
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    dispatched = [x for x in lines if x["msg"] == "dispatched"]
    assert [(x["route"], x["workflow_run_id"], x["delivery"]) for x in dispatched] == [("ci-fixer", "run-1", "d-1")]
    assert [x["route"] for x in lines if x["msg"] == "relayed"] == ["github-public"]


def test_handle_deletes_failed_dispatch_without_retry(capsys):
    cfg = dict(CFG, ROUTES=active_routes({"KEY_CI": "k1", "KEY_DOCS": "k2"}))
    failed = {"Body": envelope(event="check_run", body_b64=base64.b64encode(RAW_CHECK_FAILED).decode())}
    assert MOD.handle(failed, cfg, dify_post=lambda *a: (503, None, "busy")) is True
    line = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert (line["msg"], line["detail"], line["deleted"]) == ("dispatch_failed", "busy", True)


def test_forward_dify_streams_until_workflow_started():
    import http.server
    import threading

    seen = {}
    events = [
        b'data: {"event": "ping"}\n\n',
        b'data: {"event": "workflow_started", "workflow_run_id": "run-9", "task_id": "t"}\n\n',
    ]

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["auth"] = self.headers.get("Authorization")
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in events:
                self.wfile.write(chunk)
                self.wfile.flush()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/"
        status, run_id, detail = MOD.forward_dify(base, "k1", {"event": "check_run"}, 5)
    finally:
        server.shutdown()
    assert (status, run_id, detail) == (200, "run-9", "")
    assert seen["path"] == "/v1/workflows/run"
    assert seen["auth"] == "Bearer k1"
    assert seen["body"] == {"inputs": {"event": "check_run"}, "response_mode": "streaming", "user": "github-event-relay"}


def test_forward_dify_reports_malformed_response_without_raising():
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.wfile.write(b"not an http status line\r\n\r\n")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, run_id, detail = MOD.forward_dify(f"http://127.0.0.1:{server.server_port}/", "k1", {}, 5)
    finally:
        server.shutdown()
    assert status is None and run_id is None and detail


def test_handle_treats_deeply_nested_body_as_poison(capsys):
    deep = base64.b64encode(b"[" * 200000 + b"]" * 200000).decode()
    routes = active_routes({"KEY_CI": "k1", "KEY_DOCS": "k2"})
    assert MOD.handle({"Body": envelope(body_b64=deep)}, dict(CFG, ROUTES=routes), dify_post=lambda *a: (200, "r", "")) is False
    assert MOD.handle({"Body": envelope(body_b64=deep)}, CFG, post=lambda *a: (200, "{}")) is False
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [x["msg"] for x in lines] == ["poison", "poison"]


def test_handle_keeps_relay_running_when_dispatch_raises(capsys):
    import http.client

    cfg = dict(CFG, ROUTES=active_routes({"KEY_CI": "k1", "KEY_DOCS": "k2"}))
    failed = {"Body": envelope(event="check_run", body_b64=base64.b64encode(RAW_CHECK_FAILED).decode())}

    def boom(*args):
        raise http.client.BadStatusLine("x")

    assert MOD.handle(failed, cfg, dify_post=boom) is True
    line = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert (line["msg"], line["detail"], line["deleted"]) == ("dispatch_failed", "BadStatusLine", True)


def test_load_routes_drops_route_with_wrong_field_types(capsys):
    bad = [
        dict(ROUTE_DEFS[0], match={"event": "check_run", "action": {"x": 1}}),
        dict(ROUTE_DEFS[0], name="timeout-bad", timeout="soon"),
        dict(ROUTE_DEFS[1], name="inputs-bad", inputs=["repo"]),
    ]
    assert MOD.load_routes(json.dumps(bad), {"KEY_CI": "k", "KEY_DOCS": "k"}) == []
    reasons = [json.loads(x)["reason"] for x in capsys.readouterr().out.splitlines()]
    assert reasons == [
        "match.action must be a string or a list",
        "timeout must be a number",
        "inputs must be a mapping",
    ]


def test_forward_dify_reports_error_event_without_run_id():
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'data: {"event": "error", "message": "app unavailable"}\n\n')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, run_id, detail = MOD.forward_dify(f"http://127.0.0.1:{server.server_port}/", "k1", {}, 5)
    finally:
        server.shutdown()
    assert (status, run_id, detail) == (200, None, "app unavailable")


# --- short-lived queue credentials -------------------------------------------

BAO_CFG = {"RELAY_BAO_ADDR": "https://bao.example.test/", "RELAY_BAO_ROLE_ID": "rid",
           "RELAY_BAO_SECRET_ID": "sid", "RELAY_BAO_CREDS_PATH": "aws/sts/example-role"}


class FakeBao:
    def __init__(self, lease=900, data=None):
        self.calls, self.lease, self.n = [], lease, 0
        self.data = data

    def __call__(self, url, body, token=None):
        self.calls.append((url, body, token))
        self.n += 1
        data = self.data if self.data is not None else {
            "access_key": f"AK{self.n}", "secret_key": "sk", "security_token": "st"}
        return {"lease_duration": self.lease, "data": data}


class FakeCLI:
    def __init__(self, login_token="bao-tok"):
        self.calls = []
        self.login_token = login_token

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, f"{self.login_token}\n", "")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_credentials_refresh_before_expiry():
    bao, cli, clock = FakeBao(lease=900), FakeCLI(), Clock()
    sts = MOD.StsCredentials(BAO_CFG, http=bao, clock=clock, margin=300, run=cli)
    assert sts.needs_refresh()
    creds = sts.refresh()
    assert creds == {"aws_access_key_id": "AK1", "aws_secret_access_key": "sk", "aws_session_token": "st"}
    assert [c[0] for c in bao.calls] == ["https://bao.example.test/v1/aws/sts/example-role"]
    assert bao.calls[0][1] is None and bao.calls[0][2] == "bao-tok"
    assert [c[0] for c in cli.calls] == [
        ["bao-approle", "login"],
        ["bao-approle", "revoke-self"],
    ]
    login_args, login_kwargs = cli.calls[0]
    assert login_kwargs["env"]["BAO_APPROLE_ROLE_ID"] == "rid"
    assert login_kwargs["env"]["BAO_APPROLE_SECRET_ID"] == "sid"
    assert "sid" not in login_args
    assert login_kwargs["check"] is True and login_kwargs["capture_output"] is True
    assert cli.calls[1][1]["input"] == "bao-tok\n"
    clock.t += 599
    assert not sts.needs_refresh()
    clock.t += 1  # 600s in: 300s margin before the 900s lease ends
    assert sts.needs_refresh()
    assert sts.refresh()["aws_access_key_id"] == "AK2"


def test_credentials_reject_incomplete_or_short_lease():
    with pytest.raises(ValueError):
        MOD.StsCredentials(BAO_CFG, http=FakeBao(data={"access_key": "a", "secret_key": "s"}),
                           run=FakeCLI()).refresh()
    with pytest.raises(ValueError):
        MOD.StsCredentials(BAO_CFG, http=FakeBao(lease=120), margin=300, run=FakeCLI()).refresh()


def test_failed_read_still_revokes():
    def http(url, body, token=None):
        raise OSError("down")

    cli = FakeCLI()
    with pytest.raises(OSError):
        MOD.StsCredentials(BAO_CFG, http=http, run=cli).refresh()
    assert [call[0] for call in cli.calls] == [
        ["bao-approle", "login"],
        ["bao-approle", "revoke-self"],
    ]


def test_failed_cli_login_hides_credentials():
    def fail_login(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, stderr="secret_id leaked by mock")

    with pytest.raises(ValueError) as exc:
        MOD.StsCredentials(BAO_CFG, run=fail_login).refresh()
    assert str(exc.value) == "AppRole login failed"
    assert "sid" not in str(exc.value)


def test_login_refuses_non_https_before_cli():
    cli = FakeCLI()
    with pytest.raises(ValueError):
        MOD.StsCredentials({**BAO_CFG, "RELAY_BAO_ADDR": "http://bao.example.test"}, run=cli).refresh()
    assert cli.calls == []


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
