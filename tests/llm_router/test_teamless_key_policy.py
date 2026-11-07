"""Exercise native team assignment against a local API fixture."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from test_privacy_grants import MARKER, ROLE, run_tasks


@contextmanager
def team_api(team_info=None, keys=None):
    writes = []
    keys = {} if keys is None else {row["token"]: row for row in keys}
    team_models = (team_info or {}).get("models", ["fixture-local"])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(404 if team_info is None else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"team_info": team_info}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            writes.append((self.path, body))
            status = 200
            if self.path == "/key/update":
                key = keys[body["key"]]
                models = key.get("models", [])
                if body.get("team_id") == "default-settings" and set(models) - set(team_models):
                    status = 400
                else:
                    key.update(body)
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
            "llm_router_virtual_keys": [{"alias": "fixture-managed", "value": "sk-fixture-managed"}],
            "llm_router_existing_keys": {"json": {"keys": records}},
        }
        result = run_tasks(tmp_path, [], "reconcile-teamless-keys.yml", **variables)
        assert result.returncode == 0, result.stdout + result.stderr
        assert writes == [
            ("/key/update", {"key": records[1]["token"], "models": ["fixture-local"]}),
            *[
                ("/key/update", {"key": row["token"], "team_id": "default-settings"})
                for row in records[:2]
            ],
        ]
        assert records[1]["models"] == ["fixture-local"]
        assert records[1]["metadata"] == {"app": "fixture-app"}
        assert records[2]["models"] == ["fixture-retaining"]
        assert MARKER not in result.stdout + result.stderr
        result = run_tasks(tmp_path, [], "reconcile-teamless-keys.yml", **variables)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(writes) == 3
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


def test_team_assignment_uses_complete_snapshot_before_seeded_updates():
    seed = (ROLE / "tasks/seed-keys.yml").read_text()
    assert seed.index("seed-teams.yml") < seed.index("reconcile-teamless-keys.yml")
    assert seed.index("seed-key-pages.yml") < seed.index("reconcile-teamless-keys.yml")
    assert seed.index("reconcile-teamless-keys.yml") < seed.index("reconcile-seeded-key-policy.yml")
