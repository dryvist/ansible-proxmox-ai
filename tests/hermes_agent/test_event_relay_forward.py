"""Self-check for the event relay's streaming Dify client (forward_dify)."""
import json

from _event_relay_shared import MOD

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
