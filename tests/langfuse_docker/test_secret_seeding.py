"""Exercise Langfuse's generate-if-absent secret flow against a local KV stub."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/langfuse_docker/defaults/main/00-main.yml"
TASKS = ROOT / "roles/langfuse_docker/tasks/seed_secrets.yml"
TEST_PLAYBOOK = Path(__file__).with_name("test_secret_seeding.yml")
FIXTURE_TOKEN = "fixture-writer-token"
FIELD_NAMES = {"nextauth_secret", "salt", "encryption_key"}


class _KVState:
    def __init__(self, values: dict[str, str] | None = None):
        self.values = None if values is None else dict(values)
        self.version = 0 if values is None else 1
        self.write_count = 0
        self.lock = threading.Lock()


@contextmanager
def _openbao(values: dict[str, str] | None = None) -> Iterator[tuple[str, _KVState]]:
    state = _KVState(values)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _respond(self, status: int, payload: dict | None = None) -> None:
            body = json.dumps(payload or {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/v1/sys/health":
                self._respond(200, {"initialized": True, "sealed": False})
                return
            if path == "/v1/auth/token/lookup-self":
                if self.headers.get("X-Vault-Token") != FIXTURE_TOKEN:
                    self._respond(403, {"errors": ["denied"]})
                    return
                self._respond(
                    200,
                    {
                        "data": {
                            "policies": ["default", "ansible-converge"],
                            "identity_policies": [],
                            "renewable": False,
                            "orphan": True,
                            "ttl": 300,
                        }
                    },
                )
                return
            if path == "/v1/secret/data/ai/langfuse":
                with state.lock:
                    if state.values is None:
                        self._respond(404, {"errors": ["not found"]})
                    else:
                        self._respond(
                            200,
                            {
                                "data": {
                                    "data": state.values,
                                    "metadata": {"version": state.version},
                                }
                            },
                        )
                return
            self._respond(404, {"errors": ["not found"]})

        def do_POST(self) -> None:
            if self.headers.get("X-Vault-Token") != FIXTURE_TOKEN:
                self._respond(403, {"errors": ["denied"]})
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode()) if length else {}
            path = urlsplit(self.path).path
            if path == "/v1/sys/capabilities-self":
                capabilities = {
                    item: (["create", "read", "update"] if item.endswith("/data/ai/langfuse") else ["read"])
                    for item in payload.get("paths", [])
                }
                self._respond(200, {"data": capabilities})
                return
            if path == "/v1/secret/data/ai/langfuse":
                with state.lock:
                    expected_version = int(payload["options"]["cas"])
                    if state.values is None:
                        if expected_version != 0:
                            self._respond(400, {"errors": ["cas mismatch"]})
                            return
                        state.values = {}
                    elif expected_version != state.version:
                        self._respond(400, {"errors": ["cas mismatch"]})
                        return
                    state.values.update(payload["data"])
                    state.version += 1
                    state.write_count += 1
                    self._respond(200, {"data": {"version": state.version}})
                return
            self._respond(404, {"errors": ["not found"]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _safe_output(process: subprocess.CompletedProcess[str], values: dict[str, str]) -> str:
    output = process.stdout + process.stderr
    for value in (*values.values(), FIXTURE_TOKEN):
        output = output.replace(value, "[redacted]")
    return output


def _run_playbook(address: str, data_dir: Path) -> subprocess.CompletedProcess[str]:
    ansible_playbook = shutil.which("ansible-playbook")
    if ansible_playbook is None:
        pytest.skip("ansible-playbook is unavailable in this local environment")

    data_dir.mkdir(parents=True, exist_ok=True)
    ansible_temp = data_dir / ".ansible-tmp"
    ansible_temp.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "ANSIBLE_LOCAL_TEMP": str(ansible_temp),
            "ANSIBLE_NOCOLOR": "1",
            "OPENBAO_SEED_WRITER_TOKEN": FIXTURE_TOKEN,
            "LANGFUSE_TEST_DATA_DIR": str(data_dir),
            "LANGFUSE_TEST_OPENBAO_ADDR": address,
        }
    )
    return subprocess.run(
        [ansible_playbook, str(TEST_PLAYBOOK), "-i", "localhost,", "-c", "local"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def _write_secret_files(data_dir: Path, values: dict[str, str]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    names = {
        "nextauth_secret": ".nextauth_secret",
        "salt": ".salt",
        "encryption_key": ".encryption_key",
    }
    for field, filename in names.items():
        path = data_dir / filename
        path.write_text(values[field], encoding="utf-8")
        path.chmod(0o600)


def _read_secret_files(data_dir: Path) -> dict[str, str]:
    names = {
        "nextauth_secret": ".nextauth_secret",
        "salt": ".salt",
        "encryption_key": ".encryption_key",
    }
    return {field: _read_root_file(data_dir / filename) for field, filename in names.items()}


def _read_root_file(path: Path) -> str:
    # The role writes these root:root 0600 through become; read them the same way when the test user cannot.
    if os.access(path, os.R_OK):
        return path.read_text(encoding="utf-8")
    return subprocess.run(["sudo", "-n", "cat", str(path)], check=True, capture_output=True, text=True).stdout


def _assert_secret_files(data_dir: Path, values: dict[str, str]) -> None:
    assert _read_secret_files(data_dir) == values
    for filename in (".nextauth_secret", ".salt", ".encryption_key"):
        assert stat.S_IMODE((data_dir / filename).stat().st_mode) == 0o600


def test_secret_specs_preserve_lengths_and_hex_contract():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    specs = {item["name"]: item for item in defaults["langfuse_docker_generated_secrets"]}

    assert set(specs) == FIELD_NAMES
    assert specs["nextauth_secret"]["length"] == 48
    assert specs["nextauth_secret"]["charset"] == "alnum"
    assert specs["salt"]["length"] == 48
    assert specs["salt"]["charset"] == "alnum"
    assert specs["encryption_key"]["length"] == 64
    assert specs["encryption_key"]["charset"] == "hex"


def test_role_uses_shared_seed_writer_and_guards_existing_file_values():
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    names = [task.get("name") for task in tasks]
    seed_index = names.index("Seed Langfuse secrets in OpenBao")
    compare_index = names.index("Compare existing Langfuse secret files with OpenBao")
    failure_index = names.index("Fail if an existing Langfuse secret file differs from OpenBao")
    copy_index = names.index("Write missing Langfuse secret files from OpenBao")

    assert names.index("Read existing Langfuse secret files for import") < seed_index
    assert seed_index < compare_index < failure_index < copy_index
    assert "random_string" not in TASKS.read_text(encoding="utf-8")

    seed_block = tasks[seed_index]
    include = seed_block["block"][0]
    assert seed_block["delegate_to"] == "localhost"
    assert "delegate_facts" not in seed_block
    assert seed_block["no_log"] is True
    assert include["ansible.builtin.include_role"] == {
        "name": "dryvist.homelab.openbao_secrets",
        "tasks_from": "seed_generated",
    }
    assert include["vars"]["openbao_secrets_seed_token"] == "{{ openbao_secrets_converge_writer_token }}"
    assert "BAO_TOKEN" not in include["vars"]["openbao_secrets_seed_token"]
    assert include["vars"]["openbao_secrets_seed_expected_writer_policy"] == "ansible-converge"
    assert include["vars"]["openbao_secrets_seed_revoke_token"] is False
    assert include["vars"]["openbao_secrets_seed_path"] == "ai/langfuse"
    assert "rotate_fields" not in include["vars"]

    compare = tasks[compare_index]
    failure = tasks[failure_index]
    missing_file_copy = tasks[copy_index]
    assert compare["no_log"] is True
    assert "no_log" not in failure
    assert "langfuse_docker_secret_mismatched_fields" in failure["ansible.builtin.fail"]["msg"]
    assert missing_file_copy["no_log"] is True
    assert missing_file_copy["ansible.builtin.copy"]["force"] is False
    assert missing_file_copy["ansible.builtin.copy"]["owner"] == "root"
    assert missing_file_copy["ansible.builtin.copy"]["group"] == "root"
    assert missing_file_copy["ansible.builtin.copy"]["mode"] == "0600"


def test_first_run_generates_once_then_keeps_stored_key_and_salt(tmp_path: Path):
    data_dir = tmp_path / "langfuse-data"
    with _openbao() as (address, state):
        first = _run_playbook(address, data_dir)
        assert first.returncode == 0, _safe_output(first, {})
        assert state.values is not None
        assert set(state.values) == FIELD_NAMES
        assert re.fullmatch(r"[A-Za-z0-9]{48}", state.values["nextauth_secret"])
        assert re.fullmatch(r"[A-Za-z0-9]{48}", state.values["salt"])
        assert re.fullmatch(r"[0-9a-f]{64}", state.values["encryption_key"])
        first_values = dict(state.values)
        _assert_secret_files(data_dir, first_values)
        assert state.write_count == 1

        second = _run_playbook(address, data_dir)
        assert second.returncode == 0, _safe_output(second, first_values)
        assert state.values == first_values
        assert state.write_count == 1
        _assert_secret_files(data_dir, first_values)


def test_existing_guest_files_seed_missing_kv_fields_without_replacement(tmp_path: Path):
    data_dir = tmp_path / "langfuse-data"
    values = {
        "nextauth_secret": "fixture-nextauth-import-value",
        "salt": "fixture-salt-import-value",
        "encryption_key": "a" * 64,
    }
    _write_secret_files(data_dir, values)

    with _openbao() as (address, state):
        process = _run_playbook(address, data_dir)
        assert process.returncode == 0, _safe_output(process, values)
        assert state.values == values
        assert state.write_count == 1
        _assert_secret_files(data_dir, values)


def test_existing_kv_values_write_missing_files_without_a_kv_write(tmp_path: Path):
    data_dir = tmp_path / "langfuse-data"
    values = {
        "nextauth_secret": "fixture-nextauth-stored-value",
        "salt": "fixture-salt-stored-value",
        "encryption_key": "b" * 64,
    }

    with _openbao(values) as (address, state):
        process = _run_playbook(address, data_dir)
        assert process.returncode == 0, _safe_output(process, values)
        assert state.values == values
        assert state.write_count == 0
        _assert_secret_files(data_dir, values)


def test_existing_file_mismatch_fails_without_replacing_the_stored_value(tmp_path: Path):
    data_dir = tmp_path / "langfuse-data"
    stored = {
        "nextauth_secret": "fixture-nextauth-stored-value",
        "salt": "fixture-salt-stored-value",
        "encryption_key": "b" * 64,
    }
    mismatched_file = {**stored, "salt": "fixture-salt-different-value"}
    _write_secret_files(data_dir, mismatched_file)

    with _openbao(stored) as (address, state):
        process = _run_playbook(address, data_dir)
        assert process.returncode != 0
        output = _safe_output(process, {**stored, "mismatched_salt": mismatched_file["salt"]})
        assert "does not match OpenBao field(s): salt" in output
        assert "[redacted]" not in output
        assert state.values == stored
        assert state.write_count == 0
        assert _read_secret_files(data_dir) == mismatched_file
