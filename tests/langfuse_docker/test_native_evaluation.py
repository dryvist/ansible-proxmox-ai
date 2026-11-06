"""Run native reconciliation against a stateful local public-API fixture."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/langfuse_docker"


@pytest.mark.parametrize("scenario", [
    "create", "changed", "chat", "pagination", "duplicate", "missing-prompt", "defaults", "transient",
    "api-error", "write-error", "invalid-sampling", "invalid-price", "unknown-variable", "disabled",
])
def test_native_reconciliation(tmp_path, scenario):
    resources = {"datasets": {}, "models": [], "evaluators": [], "evaluation-rules": []}
    requests = []
    prompt = "Fixture {{input}} {{output}}"
    if scenario == "unknown-variable":
        prompt = "Fixture {{unsupported}}"
    expected_auth = "Basic " + base64.b64encode(b"fixture-public:fixture-secret").decode()
    cursor = base64.urlsafe_b64encode(json.dumps({
        "v": 1, "lastCreatedAt": "2026-01-01T00:00:00Z", "lastId": "fixture-page",
    }).encode()).decode().rstrip("=")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            requests.append(("GET", self.path, None))
            assert self.headers.get("Authorization") == expected_auth
            path = urlsplit(self.path).path
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if scenario == "transient" and len(requests) == 1:
                self.respond(503, {"message": "fixture-starting"})
            elif scenario == "api-error":
                self.respond(500, {"message": "fixture-error"})
            elif "/v2/prompts/" in path:
                assert path.endswith("/judge-fixture")
                assert query["label"] == ["production"]
                self.respond(404 if scenario == "missing-prompt" else 200, {
                    "type": "chat" if scenario == "chat" else "text",
                    "prompt": [{"role": "user", "content": prompt}] if scenario == "chat" else prompt,
                    "version": 1,
                })
            elif "/v2/datasets/" in path:
                name = path.rsplit("/", 1)[-1]
                self.respond(200 if name in resources["datasets"] else 404,
                             resources["datasets"].get(name, {}))
            else:
                key = path.rsplit("/", 1)[-1]
                rows = resources[key]
                if key == "evaluators" and scenario == "duplicate" and rows:
                    rows = rows + rows
                if key == "models":
                    page = int(query["page"][0])
                    self.respond(200, {"data": rows if page == 2 or scenario != "pagination" else [],
                                       "meta": {"totalPages": 2 if scenario == "pagination" else 1}})
                else:
                    if "cursor" in query and query["cursor"] != [cursor]:
                        self.respond(400, {"message": "Invalid cursor format"})
                        return
                    more = scenario == "pagination" and "cursor" not in query
                    self.respond(200, {"data": [] if more else rows,
                                       "meta": {"cursor": cursor} if more else {}})

        def write_resource(self):
            assert self.headers.get("Authorization") == expected_auth
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.command, self.path, body))
            if scenario == "write-error":
                self.respond(500, {"message": "fixture-write-error"})
                return
            key = self.path.rsplit("/", 1)[-1] if self.command == "POST" else self.path.split("/")[-2]
            if key == "datasets":
                resources[key][body["name"]] = body
                self.respond(200, body)
                return
            row = {**body, "id": f"fixture-{key}"}
            if key == "models":
                row.update(isLangfuseManaged=False, createdAt=body["startDate"])
            if self.command == "PATCH":
                resources[key][:] = [row]
            else:
                resources[key].append(row)
            self.respond(201 if key == "evaluators" and self.command == "POST" else 200, row)

        do_POST = write_resource
        do_PATCH = write_resource

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    variables = {
        "role_path": str(ROLE),
        "langfuse_docker_public_api_url": f"http://127.0.0.1:{server.server_port}/api/public",
        "langfuse_docker_init_public_key": "fixture-public",
        "langfuse_docker_init_secret_key": "fixture-secret",
        "langfuse_docker_native_evaluation_enabled": scenario != "disabled",
        "langfuse_docker_datasets": [{"name": "fixture-dataset"}],
        "langfuse_model_prices": [{"model_pattern": "^fixture-local$", "unit": "TOKENS",
                                   "input_price": -1 if scenario == "invalid-price" else 0,
                                   "output_price": 0}],
        "langfuse_evaluators": [{"name": "fixture-judge", "prompt_name": "judge-fixture",
                                 "model": "small", "target": {"agent": "fixture", "tier": 2}}],
    }
    if scenario == "invalid-sampling":
        variables["langfuse_evaluators"][0]["sampling_rate"] = 2
    if scenario == "defaults":
        del variables["langfuse_model_prices"]
        del variables["langfuse_evaluators"]
        variables["langfuse_docker_local_unit_price"] = 0.25
    entry = next(task for task in yaml.safe_load((ROLE / "tasks/main.yml").read_text())
                 if task.get("name") == "Reconcile native datasets, prices, and judge evaluators")
    entry["ansible.builtin.include_tasks"] = str(ROLE / "tasks/reconcile-native-evaluation.yml")
    playbook = tmp_path / "native.yml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Replay native public APIs", "hosts": "localhost", "gather_facts": False,
        "vars": variables,
        "tasks": [{"name": "Load production defaults", "ansible.builtin.include_vars": {
            "dir": str(ROLE / "defaults/main")}},
            {"name": "Select fixture inputs", "ansible.builtin.set_fact": variables}, entry],
    }], sort_keys=False))

    def run():
        return subprocess.run(
            ["ansible-playbook", str(playbook), "-i", "localhost,", "-c", "local"],
            capture_output=True, text=True, timeout=180, check=False,
            env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-tmp")},
        )

    try:
        result = run()
        errors = {"api-error", "write-error", "invalid-price", "invalid-sampling",
                  "unknown-variable", "missing-prompt"}
        assert (result.returncode != 0) == (scenario in errors), result.stdout + result.stderr
        if scenario in errors:
            assert resources["evaluators"] == []
            return
        if scenario == "disabled":
            assert requests == []
            return
        writes = [(method, path, body) for method, path, body in requests if method != "GET"]
        if scenario == "defaults":
            assert len(resources["models"]) > 1
            assert all(row["inputPrice"] == row["outputPrice"] == 0.25 for row in resources["models"])
            assert resources["evaluators"] == []
            requests.clear()
            result = run()
            assert result.returncode == 0, result.stdout + result.stderr
            assert all(method == "GET" for method, _, _ in requests)
            return
        assert len(writes) == 4
        judge = resources["evaluators"][0]
        assert judge["type"] == "llm_as_judge"
        assert judge["prompt"] == [{"role": "user", "content": prompt}]
        assert judge["modelConfig"] == {"provider": "litellm-router", "model": "small"}
        assert judge["outputDefinition"] == {"dataType": "NUMERIC", "minValue": 0, "maxValue": 1}
        assert judge["variableMapping"] == [
            {"variable": name, "source": name, "jsonPath": None} for name in ("input", "output")]
        rule = resources["evaluation-rules"][0]
        assert rule["sampling"] == 0.1
        assert rule["enabled"] is True
        assert rule["filter"] == [{"type": "arrayOptions", "column": "tags",
                                   "operator": "all of", "value": ["agent:fixture", "tier:2"]}]
        assert rule["evaluatorAssignments"] == [{"evaluatorId": judge["id"], "variableMapping": None}]
        requests.clear()
        if scenario == "changed":
            resources["models"][0]["inputPrice"] = 1
            resources["evaluators"][0]["modelConfig"]["model"] = "fixture-old"
            resources["evaluation-rules"][0]["sampling"] = 1
        result = run()
        assert (result.returncode != 0) == (scenario == "duplicate"), result.stdout + result.stderr
        writes = [(method, path) for method, path, _ in requests if method != "GET"]
        assert writes == ([
            ("POST", "/api/public/models"),
            ("PATCH", "/api/public/v2/evaluators/fixture-evaluators"),
            ("PATCH", "/api/public/v2/evaluation-rules/fixture-evaluation-rules"),
        ] if scenario == "changed" else [])
        if scenario == "changed":
            requests.clear()
            result = run()
            assert result.returncode == 0, result.stdout + result.stderr
            assert all(method == "GET" for method, _, _ in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
