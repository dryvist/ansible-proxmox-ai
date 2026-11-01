#!/usr/bin/env python3
"""Stub for POST /console/api/login: 401 until LOGIN_OK_MARKER exists, then 200.

Used by test_admin_password_reset.yml in place of a real Dify console, since
standing up the full stack (db + redis + api + migrations + an already
provisioned admin) is not practical in CI. Everything else about the login
task under test is real: the ansible.builtin.uri call, its status handling,
and the retry wiring.

Like Dify 1.17's decrypt_password_field, the password field must be the
Base64 encoding of the expected password; anything else is a 401.
"""
import base64
import binascii
import http.server
import json
import os
import sys

marker = sys.argv[1]
port = int(sys.argv[2])
expected_password = sys.argv[3]


def _password_matches(body):
    try:
        encoded = json.loads(body or b"{}").get("password", "")
        return base64.b64decode(encoded, validate=True).decode("utf-8") == expected_password
    except (ValueError, binascii.Error, UnicodeDecodeError, AttributeError):
        return False


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        ok = self.path == "/console/api/login" and os.path.exists(marker) and _password_matches(body)
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        if ok:
            self.send_header("Set-Cookie", "csrf_token=test-csrf; Path=/")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format, *args):
        pass


http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
