"""Self-check for the event relay's Dify route handling."""
import base64
import json

from _event_relay_shared import CFG, MOD, envelope


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


def test_match_routes_by_event_action_and_where():
    routes = active_routes({"KEY_CI": "k1", "KEY_DOCS": "k2"})
    assert [r["name"] for r in MOD.match_routes("check_run", RAW_CHECK_FAILED, routes)] == ["ci-fixer"]
    assert MOD.match_routes("check_run", RAW_CHECK_OK, routes) == []
    assert [r["name"] for r in MOD.match_routes("pull_request", RAW_MERGED, routes)] == ["docs-updater"]
    assert MOD.match_routes("pull_request", RAW_CLOSED_UNMERGED, routes) == []
    assert MOD.match_routes("push", RAW_MERGED, routes) == []
    assert MOD.match_routes("check_run", RAW_CHECK_FAILED, []) == []


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


def test_dify_inputs_sends_booleans_as_strings():
    mapping = {"merged": "pull_request.merged", "pr_number": "pull_request.number"}
    assert MOD.dify_inputs("pull_request", RAW_MERGED, mapping) == {
        "event": "pull_request", "merged": "true", "pr_number": 7,
    }


def test_finder_route_stays_off_without_its_key():
    finder = {
        "name": "doc-contradiction-finder", "base": "https://dify.example", "app_key_env": "KEY_FINDER", "timeout": 30,
        "match": {"event": "pull_request", "action": "closed", "where": {"pull_request.merged": True}},
        "inputs": {"action": "action", "merged": "pull_request.merged", "org": "repository.owner.login",
                   "repo": "repository.name", "pr_number": "pull_request.number"},
    }
    assert MOD.load_routes(json.dumps([finder]), {}) == []
    assert [r["name"] for r in MOD.load_routes(json.dumps([finder]), {"KEY_FINDER": "k4"})] == [
        "doc-contradiction-finder"]


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


def merged_pair(env):
    # Two routes that both match a merged pull request, each with its own app key.
    second = dict(ROUTE_DEFS[1], name="second", app_key_env="KEY_SECOND")
    return MOD.load_routes(json.dumps([ROUTE_DEFS[1], second]), env)


def merged_event(delivery):
    return {"Body": envelope(event="pull_request", body_b64=base64.b64encode(RAW_MERGED).decode(), delivery=delivery)}


def test_handle_fires_every_matching_route(capsys):
    cfg = dict(CFG, ROUTES=merged_pair({"KEY_DOCS": "k2", "KEY_SECOND": "k3"}))
    calls = []

    def dify(base, key, inputs, timeout):
        calls.append(key)
        return 200, f"run-{key}", ""

    assert MOD.handle(merged_event("d-7"), cfg, dify_post=dify) is True
    assert calls == ["k2", "k3"]
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [(x["route"], x["msg"], x["delivery"]) for x in lines] == [
        ("docs-updater", "dispatched", "d-7"), ("second", "dispatched", "d-7")]


def test_handle_one_failing_route_does_not_stop_the_other(capsys):
    cfg = dict(CFG, ROUTES=merged_pair({"KEY_DOCS": "k2", "KEY_SECOND": "k3"}))
    calls = []

    def dify(base, key, inputs, timeout):
        calls.append(key)
        if key == "k2":
            raise OSError("connection reset")
        return 200, "run-k3", ""

    assert MOD.handle(merged_event("d-8"), cfg, dify_post=dify) is True
    assert calls == ["k2", "k3"]
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [(x["route"], x["msg"], x["delivery"], x["detail"]) for x in lines] == [
        ("docs-updater", "dispatch_failed", "d-8", "OSError"), ("second", "dispatched", "d-8", None)]


def test_handle_no_match_fires_no_route_and_takes_default(capsys):
    cfg = dict(CFG, ROUTES=merged_pair({"KEY_DOCS": "k2", "KEY_SECOND": "k3"}))
    dify_calls, default_calls = [], []

    def dify(base, key, inputs, timeout):
        dify_calls.append(key)
        return 200, "run", ""

    def default_post(url, secret, event, delivery, raw):
        default_calls.append(url)
        return 200, "{}"

    assert MOD.handle({"Body": envelope()}, cfg, post=default_post, dify_post=dify) is True
    assert dify_calls == []
    assert default_calls == ["http://127.0.0.1:1/webhooks/github-public"]


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
