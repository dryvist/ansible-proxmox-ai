"""Exercise native team assignment against a local API fixture."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import pytest

from test_privacy_grants import MARKER, ROLE, run_tasks


@contextmanager
def team_api(team_info=None, keys=None, users=(), members=None, reject_keys=()):
    writes = []
    keys = {} if keys is None else {row["token"]: row for row in keys}
    team_models = (team_info or {}).get("models", ["fixture-local"])
    members = {} if members is None else members

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/user/info":
                owner = parse_qs(parsed.query)["user_id"][0]
                status = 200 if owner in users else 404
                response = {"user_info": {"user_id": owner}} if status == 200 else {}
            else:
                team_id = parse_qs(parsed.query)["team_id"][0]
                status = 404 if team_info is None and not keys else 200
                response = {"team_info": {**(team_info or {}), "members_with_roles": [
                    {"user_id": owner, "role": "user"} for owner in members.get(team_id, [])
                ]}}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            writes.append((self.path, body))
            status = 200
            if self.path == "/key/update":
                key = keys[body["key"]]
                models = key.get("models", [])
                if body.get("team_id") and set(models) - set(team_models):
                    status = 400
                elif body.get("team_id") and key.get("user_id") and key["user_id"] not in members.get(body["team_id"], []):
                    status = 403
                elif body.get("team_id") and body["key"] in reject_keys:
                    status = 503
                else:
                    key.update(body)
            elif self.path == "/team/member_add":
                owner = body["member"]["user_id"]
                members.setdefault(body["team_id"], []).append(owner)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, writes
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_existing_teamless_keys_acquire_default_team_once(tmp_path):
    records = [
        {"token": MARKER, "models": [], "metadata": {}},
        {"token": MARKER + "-existing", "team_id": None,
         "models": ["fixture-retaining"], "metadata": {"app": "fixture-app"}},
        {"token": MARKER + "-public", "team_id": "fixture-public-team",
         "models": ["fixture-retaining"], "metadata": {"privacy_class": "public"}},
        {"token": MARKER + "-managed", "key_alias": "fixture-managed"},
    ]
    with team_api(keys=records) as (port, writes):
        variables = {
            "llm_router_api_port": port,
            "llm_router_master_key": "sk-fixture-master",
            "llm_router_virtual_keys": [{"alias": "fixture-managed", "value": "sk-fixture-managed", "team_id": "tier-1"}],
            "llm_router_existing_keys": {"json": {"keys": records}},
        }
        result = run_tasks(tmp_path, [], "reconcile-teamless-keys.yml", **variables)
        assert result.returncode == 0, result.stdout + result.stderr
        assert writes == [
            ("/key/update", {"key": records[0]["token"], "team_id": "default-settings"}),
            ("/key/update", {"key": records[1]["token"], "models": ["fixture-local"]}),
            ("/key/update", {"key": records[1]["token"], "team_id": "default-settings"}),
            ("/key/update", {"key": records[3]["token"], "team_id": "tier-1"}),
        ]
        assert records[1]["models"] == ["fixture-local"]
        assert records[1]["metadata"] == {"app": "fixture-app"}
        assert records[2]["models"] == ["fixture-retaining"]
        assert MARKER not in result.stdout + result.stderr
        result = run_tasks(tmp_path, [], "reconcile-teamless-keys.yml", **variables)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(writes) == 4
        assert "changed=0" in result.stdout


def test_team_api_rejects_retaining_grants_on_transfer():
    key = {"token": MARKER, "models": ["fixture-retaining"]}
    with team_api(keys=[key]) as (port, _writes):
        request = Request(
            f"http://127.0.0.1:{port}/key/update",
            data=json.dumps({"key": MARKER, "team_id": "default-settings"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError, match="400"):
            urlopen(request, timeout=5)
    assert "team_id" not in key


def test_managed_teamless_key_narrows_before_combined_seed_update(tmp_path):
    key = {"token": MARKER, "key_alias": "fixture-managed", "models": ["fixture-retaining"]}
    desired = {"alias": key["key_alias"], "value": MARKER,
               "team_id": "tier-0", "models": ["fixture-local"]}
    with team_api(keys=[key]) as (port, writes):
        variables = {
            "llm_router_api_port": port, "llm_router_master_key": "sk-fixture-master",
            "llm_router_virtual_keys": [desired], "llm_router_existing_keys": {"json": {"keys": [key]}},
            "llm_router_mcp_default_object_permission": {"mcp_servers": []},
            "_llm_router_key_trust_metadata": {},
        }
        result = run_tasks(tmp_path, [], "reconcile-teamless-keys.yml", **variables)
        assert result.returncode == 0, result.stdout + result.stderr
        assert writes == [
            ("/key/update", {"key": MARKER, "models": ["fixture-local"]}),
            ("/key/update", {"key": MARKER, "team_id": "tier-0"}),
        ]
        result = run_tasks(tmp_path, [], "reconcile-seeded-key-policy.yml", **variables)
        assert result.returncode == 0, result.stdout + result.stderr
        assert key["team_id"] == "tier-0"
        assert key["models"] == ["fixture-local"]
        assert MARKER not in result.stdout + result.stderr


@pytest.mark.parametrize("exists", [False, True])
def test_default_team_reconciles_required_tags_even_when_models_match(tmp_path, exists):
    desired = {
        "team_id": "default-settings", "models": ["fixture-local"],
        "metadata": {"tags": ["&zdr", "&sensitivity:sensitive", "&tier-1"]},
        "router_settings": {"enable_tag_filtering": True},
    }
    current = {**desired, "metadata": {"ui": "preserved"}, "router_settings": {"num_retries": 2}}
    with team_api(current if exists else None) as (port, writes):
        variables = {
            "llm_router_api_port": port, "llm_router_master_key": "sk-fixture-master",
            "llm_router_default_team_settings": [desired],
        }
        result = run_tasks(tmp_path, [], "seed-teams.yml", **variables)
        assert result.returncode == 0, result.stdout + result.stderr
        expected = desired if not exists else {
            **desired, "metadata": {"ui": "preserved", **desired["metadata"]},
            "router_settings": {"num_retries": 2, **desired["router_settings"]},
        }
        assert writes == [("/team/update" if exists else "/team/new", expected)]


@pytest.mark.parametrize("has_team", [False, True])
@pytest.mark.parametrize("mode", ["initial", "rebuild"])
def test_public_key_restores_grants_after_narrowing_in_same_converge(tmp_path, has_team, mode):
    models = ["fixture-local", "fixture-retaining", "fixture-free"]
    key = {"token": MARKER, "key_alias": "fixture-public", "models": models,
           "user_id": "fixture-owner", "metadata": {"privacy_class": "public"}}
    if has_team:
        key["team_id"] = "tier-1"
    desired = {"alias": key["key_alias"], "value": MARKER, "team_id": "tier-1-public",
               "models": models, "privacy_class": "public", "sensitivity_tag": "&sensitivity:public"}
    with team_api(team_info={"models": models}, keys=[key], users=[key["user_id"]]) as (port, writes):
        variables = {
            "llm_router_api_port": port, "llm_router_master_key": "sk-fixture-master",
            "llm_router_virtual_keys": [desired], "llm_router_existing_keys": {"json": {"keys": [key]}},
            "llm_router_mcp_default_object_permission": {"mcp_servers": []},
            "llm_router_seed_mode": mode,
            "_llm_router_key_trust_metadata": {},
        }
        tasks = ["reconcile-teamless-keys.yml", "reconcile-seeded-key-policy.yml"]
        result = run_tasks(tmp_path, [], tasks, **variables)
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert key["team_id"] == desired["team_id"]
        assert key["models"] == models
        narrow = ("/key/update", {"key": MARKER, "models": ["fixture-local"]})
        transfer = ("/key/update", {"key": MARKER, "team_id": desired["team_id"]})
        restore = next((path, body) for path, body in writes if body.get("models") == models)
        assert writes.index(narrow) < writes.index(transfer) < writes.index(restore)
        assert MARKER not in output
        count = len(writes)
        result = run_tasks(tmp_path, [], tasks, **variables)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(writes) == count


def test_team_assignment_uses_complete_snapshot_before_seeded_updates():
    seed = (ROLE / "tasks/seed-keys.yml").read_text()
    assert seed.index("seed-teams.yml") < seed.index("reconcile-teamless-keys.yml")
    assert seed.index("seed-key-pages.yml") < seed.index("reconcile-teamless-keys.yml")
    assert seed.index("reconcile-teamless-keys.yml") < seed.index("reconcile-seeded-key-policy.yml")
