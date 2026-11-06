"""Roster projection and loopback protocol contract; no deployed Hermes runtime."""

import ast
import json
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import urlsplit

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from test_a2a_config import ROLE_ROOT


DEFAULTS = yaml.safe_load((ROLE_ROOT / "defaults/main/95-a2a.yml").read_text())
ENV = Environment(
    loader=FileSystemLoader(ROLE_ROOT / "templates"), undefined=StrictUndefined,
)
ENV.filters.update(bool=bool, to_json=json.dumps)
ROSTER = {
    "source": {"trust_tier": "trusted", "a2a_peers": ["peer", "other", "source", "missing"]},
    "peer": {"trust_tier": "trusted", "a2a_callers": ["source"], "a2a_url": "https://127.0.0.1/"},
    "other": {"trust_tier": "untrusted", "a2a_callers": ["source"], "a2a_url": "https://127.0.0.1/"},
}


def context(enabled=True, profile="orchestrator", roster=None):
    ctx = {
        "ai_agents": ROSTER if roster is None else roster,
        "hermes_agent_id": "source",
        "hermes_agent_env_prefix": "SOURCE",
        "hermes_agent_profile": {"name": profile, "toolsets": {"cli": [], "cron": []}},
        "hermes_agent_a2a_delegation_enabled": enabled,
        "hermes_agent_a2a_delegating_profiles": ["orchestrator", "eval"],
        "bao_apps_secrets": {"peer_a2a_token": "fixture-peer-token"},
        "hermes_agent_a2a_peers": [{"token": "fixture-default-token"}],
    }
    for key in ("hermes_agent_a2a_allowed_peer_names", "hermes_agent_a2a_authorized_peers"):
        ctx[key] = ast.literal_eval(ENV.from_string(DEFAULTS[key]).render(**ctx))
    return ctx


def test_roster_requires_explicit_reciprocal_grant_and_secure_endpoint():
    assert DEFAULTS["hermes_agent_a2a_delegation_enabled"] is False
    assert DEFAULTS["hermes_agent_a2a_delegating_profiles"] == ["orchestrator"]
    assert context()["hermes_agent_a2a_allowed_peer_names"] == ["peer"]
    assert context(False)["hermes_agent_a2a_authorized_peers"] == []
    for change in ({"trust_tier": "untrusted"}, {"a2a_callers": []}, {"a2a_url": "http://127.0.0.1/"}):
        roster = {**ROSTER, "peer": {**ROSTER["peer"], **change}}
        assert context(roster=roster)["hermes_agent_a2a_authorized_peers"] == []
    roster = {**ROSTER, "source": {**ROSTER["source"], "trust_tier": "untrusted"}}
    assert context(roster=roster)["hermes_agent_a2a_authorized_peers"] == []


@pytest.mark.parametrize("profile,enabled,expected", [
    ("orchestrator", True, True), ("orchestrator", False, False),
    ("github-maint", True, False), ("eval", True, False),
])
def test_profile_config_and_credentials_are_scoped(profile, enabled, expected):
    ctx = context(enabled, profile)
    rendered = ENV.get_template("config-profile-a2a.yaml.j2").render(**ctx)
    assert bool(yaml.safe_load(rendered)) == expected
    assert "fixture-peer-token" not in rendered
    env = ENV.get_template("hermes-env-profile-a2a.j2").render(**ctx)
    assert ("fixture-peer-token" in env) == expected
    assert "A2A_BEARER_TOKEN=\n" in env
    assert "SOURCE_A2A_PEER_0_TOKEN=\n" in env
    assert "fixture-default-token" not in env
    # Only native name/url/auth fields are sent; no bank IDs or memory grants.
    if expected:
        peer = yaml.safe_load(rendered)["a2a_agents"]["peer"]
        assert set(peer) == {"url", "auth"}
        assert peer["auth"]["token"] == "${SOURCE_PROFILE_A2A_PEER_0_TOKEN}"


def test_profile_toolset_includes_native_a2a_only_when_authorized():
    source = (ROLE_ROOT / "templates/config-profile.yaml.j2").read_text()
    fragment = source[source.index("{% set _profile_a2a_on"):source.index("terminal:")]
    for profile, expected in (("orchestrator", True), ("eval", False), ("github-maint", False)):
        rendered = yaml.safe_load(ENV.from_string(fragment).render(
            **context(profile=profile), hermes_agent_cron_toolsets=["web"],
        ))
        assert ("a2a" in rendered["platform_toolsets"]["cli"]) == expected
        assert ("a2a" in rendered["platform_toolsets"]["cron"]) == expected


def test_authenticated_request_result_and_missing_or_wrong_bearer_denial():
    """Exercise rendered credentials over HTTP to a fixture native-A2A boundary.

    TLS termination and Hermes execution require a separate deployment receipt.
    """
    received = []

    class Peer(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get("Authorization") != "Bearer fixture-peer-token":
                self.send_error(401)
                return
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(request)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({
                "jsonrpc": "2.0", "id": request["id"],
                "result": {"status": {"state": "completed"}, "id": "task-fixture"},
            }).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Peer)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        ctx = context()
        config = yaml.safe_load(ENV.get_template("config-profile-a2a.yaml.j2").render(**ctx))
        env = dict(line.split("=", 1) for line in
                   ENV.get_template("hermes-env-profile-a2a.j2").render(**ctx).splitlines()
                   if line and not line.startswith("#"))
        peer = config["a2a_agents"]["peer"]
        token = env[peer["auth"]["token"][2:-1]]
        payload = json.dumps({"jsonrpc": "2.0", "id": "request-fixture", "method": "message/send",
                              "params": {"message": {"role": "user", "messageId": "message-fixture",
                                         "parts": [{"kind": "text", "text": "Synthetic task"}]}}})
        for bearer, status in ((token, 200), ("unauthorized-peer", 401), (None, 401)):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                headers = {"Content-Type": "application/json"}
                if bearer:
                    headers["Authorization"] = "Bearer " + bearer
                connection.request("POST", urlsplit(peer["url"]).path, payload, headers)
                response = connection.getresponse()
                assert response.status == status
                body = response.read()
                if status == 200:
                    result = json.loads(body)
                    assert result["id"] == "request-fixture"
                    assert result["result"]["status"]["state"] == "completed"
            finally:
                connection.close()
        assert len(received) == 1
        assert "other" not in config["a2a_agents"]
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
