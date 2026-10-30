from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Thread
from typing import Iterator

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
MODE = "hermes_agent_splunk_memory_retain_extraction_mode"
CONFIG_PATH = "/v1/default/banks/hermes-splunk/config"
API_KEY = "fixture-api-key"


def _mode_block() -> dict | None:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "config_and_content.yml").read_text())
    return next(
        (task for task in tasks if task.get("name") == "Manage Splunk memory bank extraction mode"),
        None,
    )


def test_mode_is_declared_and_the_reconcile_tasks_are_safe() -> None:
    defaults = yaml.safe_load((ROLE_ROOT / "defaults" / "main" / "70-memory.yml").read_text())
    assert defaults.get(MODE) == "chunks"

    block = _mode_block()
    assert block is not None
    tasks = {task["name"]: task for task in block["block"]}
    get = tasks["Read the Splunk memory bank configuration"]
    patch = tasks["Set the Splunk memory bank extraction mode"]
    readback = tasks["Read back the Splunk memory bank extraction mode"]

    assert patch.get("notify") is None
    assert patch["no_log"] is True
    assert "not ansible_check_mode" in str(patch["when"])
    for task in (get, readback):
        assert task["check_mode"] is False
        assert task["changed_when"] is False
        assert task["no_log"] is True


class _State:
    def __init__(self) -> None:
        self.default_mode = "concise"
        self.override: str | None = None
        self.requests: list[tuple[str, str, dict | None]] = []

    def response(self) -> dict:
        overrides = {} if self.override is None else {"retain_extraction_mode": self.override}
        return {
            "overrides": overrides,
            "config": {"retain_extraction_mode": self.override or self.default_mode},
        }


@contextmanager
def _config_api() -> Iterator[tuple[str, _State]]:
    state = _State()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _valid_request(self) -> bool:
            return self.path == CONFIG_PATH and self.headers.get("Authorization") == f"Bearer {API_KEY}"

        def do_GET(self) -> None:
            state.requests.append(("GET", self.path, None))
            self._reply(200, state.response()) if self._valid_request() else self._reply(404, {})

        def do_PATCH(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            state.requests.append(("PATCH", self.path, body))
            if not self._valid_request() or body != {"updates": {"retain_extraction_mode": "chunks"}}:
                self._reply(400, {"detail": "unexpected config request"})
                return
            state.override = body["updates"]["retain_extraction_mode"]
            self._reply(200, state.response())

        def log_message(self, *_: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_real_ansible_play_reconciles_bank_config_idempotently() -> None:
    assert shutil.which("ansible-playbook"), "run this test inside the repository dev shell"
    block = _mode_block()
    assert block is not None, "the production bank config task block must exist"

    with _config_api() as (api_url, state), tempfile.TemporaryDirectory(prefix="splunk-bank-mode-") as temp_dir:
        playbook_path = Path(temp_dir) / "playbook.yml"

        def run(mode: str = "chunks", *, check: bool = False) -> subprocess.CompletedProcess[str]:
            play = [{
                "name": "Exercise the production bank config tasks",
                "hosts": "localhost",
                "gather_facts": False,
                "connection": "local",
                "vars": {
                    "hermes_agent_splunk_memory_bank_id": "hermes-splunk",
                    "hermes_agent_memory_provider": "hindsight",
                    MODE: mode,
                    "hermes_agent_memory_api_url": api_url,
                    "hermes_agent_memory_api_key": API_KEY,
                },
                "tasks": [block],
            }]
            playbook_path.write_text(yaml.safe_dump(play, sort_keys=False))
            command = ["ansible-playbook", "--inventory", "localhost,", str(playbook_path)]
            if check:
                command.append("--check")
            return subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)

        first = run()
        assert first.returncode == 0, first.stdout + first.stderr
        assert "changed=1" in first.stdout
        patches = [request for request in state.requests if request[0] == "PATCH"]
        assert patches == [("PATCH", CONFIG_PATH, {"updates": {"retain_extraction_mode": "chunks"}})]
        assert all(path == CONFIG_PATH for _, path, _ in state.requests)
        assert API_KEY not in first.stdout + first.stderr

        state.requests.clear()
        second = run()
        assert second.returncode == 0, second.stdout + second.stderr
        assert "changed=0" in second.stdout
        assert not any(method == "PATCH" for method, _, _ in state.requests)

        state.requests.clear()
        empty = run("")
        assert empty.returncode == 0, empty.stdout + empty.stderr
        assert not state.requests

        state.override = "chunks"
        state.requests.clear()
        checked = run(check=True)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert not any(method == "PATCH" for method, _, _ in state.requests)

        state.override = None
        state.requests.clear()
        pending = run(check=True)
        assert pending.returncode == 0, pending.stdout + pending.stderr
        assert "failed=0" in pending.stdout
        assert not any(method == "PATCH" for method, _, _ in state.requests)
