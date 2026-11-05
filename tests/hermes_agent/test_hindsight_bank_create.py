from __future__ import annotations

import ipaddress
import json
import shutil
import socket
import subprocess
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Iterator

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
BLOCK_NAME = "Ensure the Hindsight memory bank exists"
BANK_ID = "hermes-private"
BANK_PATH = f"/v1/default/banks/{BANK_ID}"
CONFIG_PATH = f"{BANK_PATH}/config"
API_KEY = "fixture-api-key"
LOOPBACK = str(ipaddress.IPv4Address(socket.INADDR_LOOPBACK))
BANK_JSON = {"bank_id": BANK_ID, "config": {}, "overrides": {}}


def _block() -> dict:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "config_and_content.yml").read_text())
    block = next((task for task in tasks if task.get("name") == BLOCK_NAME), None)
    assert block is not None, "the bank ensure block must exist"
    return block


class _State:
    def __init__(self, bank_exists: bool) -> None:
        self.bank_exists = bank_exists
        self.requests: list[tuple[str, str, dict | None]] = []


@contextmanager
def _bank_api(bank_exists: bool) -> Iterator[tuple[str, _State]]:
    state = _State(bank_exists)

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _authorized(self) -> bool:
            return self.headers.get("Authorization") == f"Bearer {API_KEY}"

        def do_GET(self) -> None:
            state.requests.append(("GET", self.path, None))
            if not self._authorized() or self.path != CONFIG_PATH:
                self._reply(401, {"detail": "unexpected request"})
            elif state.bank_exists:
                self._reply(200, BANK_JSON)
            else:
                self._reply(404, {"detail": f"Bank '{BANK_ID}' not found"})

        def do_PUT(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            state.requests.append(("PUT", self.path, body))
            if not self._authorized() or self.path != BANK_PATH or body != {}:
                self._reply(400, {"detail": "unexpected create request"})
                return
            state.bank_exists = True
            self._reply(200, BANK_JSON)

        def log_message(self, *_: object) -> None:
            return

    server = HTTPServer((LOOPBACK, 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{LOOPBACK}:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _run_block(
    tmp_path: Path,
    api_url: str,
    *,
    bank_id: str = BANK_ID,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    assert shutil.which("ansible-playbook"), "run this test inside the repository dev shell"
    play = [{
        "name": "Exercise the production Hindsight bank tasks",
        "hosts": "localhost",
        "gather_facts": False,
        "connection": "local",
        "vars": {
            "hermes_agent_memory_provider": "hindsight",
            "hermes_agent_hindsight_config_bank_id": bank_id,
            "hermes_agent_hindsight_config_api_url": api_url,
            "hermes_agent_hindsight_config_api_key": API_KEY,
        },
        "tasks": [_block()],
    }]
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(yaml.safe_dump(play, sort_keys=False))
    command = ["ansible-playbook", "--inventory", "localhost,", str(playbook)]
    if check:
        command.append("--check")
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)


def test_existing_bank_is_read_and_not_created(tmp_path: Path) -> None:
    with _bank_api(bank_exists=True) as (api_url, state):
        result = _run_block(tmp_path, api_url)
        assert result.returncode == 0, result.stdout + result.stderr
        assert state.requests == [("GET", CONFIG_PATH, None)]
        assert not any(method == "PUT" for method, _, _ in state.requests)
        assert "changed=0" in result.stdout
        assert API_KEY not in result.stdout + result.stderr


def test_missing_bank_is_created_with_put_to_bank_path_and_empty_body(tmp_path: Path) -> None:
    with _bank_api(bank_exists=False) as (api_url, state):
        result = _run_block(tmp_path, api_url)
        assert result.returncode == 0, result.stdout + result.stderr
        assert state.requests == [("GET", CONFIG_PATH, None), ("PUT", BANK_PATH, {})]
        assert state.bank_exists
        assert "changed=1" in result.stdout
        assert API_KEY not in result.stdout + result.stderr


def test_second_run_with_bank_present_makes_no_create_call(tmp_path: Path) -> None:
    with _bank_api(bank_exists=False) as (api_url, state):
        first = _run_block(tmp_path, api_url)
        assert first.returncode == 0, first.stdout + first.stderr
        assert "changed=1" in first.stdout
        state.requests.clear()
        second = _run_block(tmp_path, api_url)
        assert second.returncode == 0, second.stdout + second.stderr
        assert "changed=0" in second.stdout
        assert state.requests == [("GET", CONFIG_PATH, None)]


def test_check_mode_reports_missing_bank_without_creating(tmp_path: Path) -> None:
    with _bank_api(bank_exists=False) as (api_url, state):
        result = _run_block(tmp_path, api_url, check=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert state.requests == [("GET", CONFIG_PATH, None)]
        assert not state.bank_exists
        assert "a converge run would create it" in result.stdout


def test_empty_bank_id_makes_no_request(tmp_path: Path) -> None:
    with _bank_api(bank_exists=False) as (api_url, state):
        result = _run_block(tmp_path, api_url, bank_id="")
        assert result.returncode == 0, result.stdout + result.stderr
        assert state.requests == []
        assert "skipped=" in result.stdout
