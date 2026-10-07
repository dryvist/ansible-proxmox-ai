"""Replay the production reconciler against local API response fixtures."""

import base64
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
from threading import Thread

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles/langfuse_docker"
CAPTURE = Path(__file__).parent / "fixtures/api-response-shapes.json"


@pytest.mark.parametrize(
    "scenario",
    [
        "disabled", "absent", "unchanged", "source-drift", "item-drift",
        "duplicate-evaluator", "evaluator-drift",
        "dataset-error", "items-error", "evaluators-error",
    ],
)
def test_stage0_reconciliation(tmp_path, scenario):
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    bundle = json.loads((ROLE / "files/datasets/typed-decisions-stage0.json").read_text())
    source = (ROLE / "files/evaluators/typed-decisions.ts").read_text().rstrip()
    captured = json.loads(CAPTURE.read_text())
    name = defaults["langfuse_docker_stage0_dataset_name"]
    dataset = {"name": name, "metadata": {
        "source_revision": bundle["revision"], "source_config": bundle["config"],
        "source_split": bundle["split"], "source_length": bundle["length"],
        "source_data_sha256": defaults["langfuse_docker_stage0_dataset_sha256"],
    }}
    items = [{
        "id": "typed-decisions-stage0-" + item["row"]["id"],
        "input": {"state": json.loads(item["row"]["state"]),
                  "questions": json.loads(item["row"]["questions"])},
        "expectedOutput": json.loads(item["row"]["gold"]),
    } for item in bundle["rows"]]
    evaluator = {"id": "fixture-evaluator", "name": defaults["langfuse_docker_stage0_evaluator_name"],
                 "sourceCode": source}
    if scenario == "source-drift":
        dataset["metadata"]["source_revision"] = "fixture-different-revision"
    if scenario == "item-drift":
        items[0]["expectedOutput"] = {"fixture": "different-answer"}
    if scenario == "evaluator-drift":
        evaluator["sourceCode"] = "fixture-previous-source"
    requests = []
    expected_auth = "Basic " + base64.b64encode(b"fixture-public:fixture-secret").decode()

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
            if self.headers.get("Authorization") != expected_auth:
                self.respond(401, {"message": "fixture-auth-required"})
                return
            if self.path.startswith("/api/public/v2/datasets/"):
                key, body = "dataset", dataset
            elif self.path.startswith("/api/public/dataset-items?"):
                key, body = "dataset_items", {"data": [] if scenario == "absent" else items}
            elif self.path.startswith("/api/public/v2/evaluators?"):
                rows = [] if scenario == "absent" else [evaluator]
                if scenario == "duplicate-evaluator":
                    rows = rows + [copy.deepcopy(evaluator)]
                key, body = "evaluators", {"data": rows}
            else:
                self.respond(404, {"message": "fixture-unexpected-path"})
                return
            failure = {"dataset-error": "dataset", "items-error": "dataset_items",
                       "evaluators-error": "evaluators"}.get(scenario)
            if key == failure:
                self.respond(captured[key]["status"], captured[key]["body"])
            elif key == "dataset" and scenario == "absent":
                self.respond(404, {"message": "fixture-dataset-absent"})
            else:
                self.respond(200, body)

        def record_write(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.command, self.path, body))
            self.respond(201 if self.path == "/api/public/v2/evaluators" else 200, body)

        do_POST = record_write
        do_PATCH = record_write

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    variables = {
        "role_path": str(ROLE), "langfuse_docker_web_port": server.server_port,
        "langfuse_docker_init_public_key": "fixture-public",
        "langfuse_docker_init_secret_key": "fixture-secret",
    }
    if scenario != "disabled":
        variables["langfuse_docker_stage0_eval_reconcile_enabled"] = True
    reconcile = next(task for task in yaml.safe_load((ROLE / "tasks/main.yml").read_text())
                     if task.get("name") == "Reconcile the Stage 0 typed-decisions dataset and evaluator")
    reconcile["ansible.builtin.include_tasks"] = str(ROLE / "tasks/reconcile-stage0-evaluation.yml")
    playbook = tmp_path / "reconcile.yml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Replay Stage 0 reconciliation", "hosts": "localhost", "gather_facts": False,
        "vars": variables,
        "tasks": [
            {"name": "Load production defaults", "ansible.builtin.include_vars": {
                "file": str(ROLE / "defaults/main.yml")}},
            {"name": "Select the local fixture endpoint", "ansible.builtin.set_fact": {
                key: value for key, value in variables.items() if key != "role_path"}},
            reconcile,
        ],
    }], sort_keys=False))
    try:
        result = subprocess.run(
            ["ansible-playbook", str(playbook), "-i", "localhost,", "-c", "local"],
            capture_output=True, text=True, check=False, timeout=90,
            env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-tmp")},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    fails = scenario in {"source-drift", "item-drift", "duplicate-evaluator",
                         "dataset-error", "items-error", "evaluators-error"}
    assert (result.returncode != 0) == fails, result.stdout + result.stderr
    writes = [(method, path, body) for method, path, body in requests if method != "GET"]
    if scenario == "absent":
        assert len(writes) == 22
        assert writes[0][1:] == ("/api/public/v2/datasets", {
            **dataset, "description": "Pinned 20-row test subset for Stage 0 System One evaluation.",
            "metadata": {**dataset["metadata"], "source": "LocalLLaMA/typed-decisions"},
        })
        assert [body["id"] for _, path, body in writes if path == "/api/public/dataset-items"] == [
            item["id"] for item in items
        ]
        for (_, _, body), item in zip(writes[1:-1], items, strict=True):
            assert body["datasetName"] == name
            assert body["input"] == item["input"]
            assert body["expectedOutput"] == item["expectedOutput"]
        assert writes[-1][2]["sourceCode"] == source
    elif scenario == "evaluator-drift":
        assert writes == [("PATCH", "/api/public/v2/evaluators/fixture-evaluator", {
            "type": "code", "sourceCodeLanguage": "TYPESCRIPT", "sourceCode": source,
        })]
    else:
        assert writes == []
    if scenario == "disabled":
        assert requests == []
