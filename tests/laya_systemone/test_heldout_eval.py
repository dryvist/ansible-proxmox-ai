"""Contracts and local HTTP behavior for Laya held-out evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles/laya_systemone"
TASKS = ROLE / "tasks"
SCRIPT = ROLE / "files/heldout_eval.py"
PLAYBOOK = REPO_ROOT / "playbooks/llm-serving-gpu-engines.yml"


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _included_file(task: dict) -> str:
    include = task["ansible.builtin.include_tasks"]
    return include if isinstance(include, str) else include["file"]


def _walk(tasks: list[dict]):
    for task in tasks:
        yield task
        for section in ("block", "rescue", "always"):
            nested = task.get(section)
            if isinstance(nested, list):
                yield from _walk(nested)


def test_heldout_defaults_use_separate_read_and_write_paths_and_bound_runtime() -> None:
    defaults = _load_yaml(ROLE / "defaults/main/30-heldout.yml")
    finetune_defaults = _load_yaml(ROLE / "defaults/main/20-finetune.yml")

    assert defaults["laya_systemone_heldout_results_bao_kv_path"] == "rustfs/system1/rw"
    assert finetune_defaults["laya_systemone_finetune_bao_kv_path"] == "rustfs/system1/ro"
    assert defaults["laya_systemone_heldout_batch_size"] == 16
    assert defaults["laya_systemone_heldout_request_timeout_seconds"] == 60
    assert defaults["laya_systemone_heldout_run_timeout_seconds"] == 360
    assert defaults["laya_systemone_heldout_total_budget_seconds"] == 540


def test_serving_tag_does_not_select_the_heldout_component() -> None:
    plays = _load_yaml(PLAYBOOK)
    serving_play = next(play for play in plays if play.get("name") == "Configure the Laya classifier guest")
    heldout_play = next(play for play in plays if play.get("name") == "Score Laya models on held-out data")

    assert "laya_systemone" in serving_play["tags"]
    assert heldout_play["tags"] == ["never", "laya_systemone_heldout"]
    assert "laya_systemone" not in heldout_play["tags"]
    assert not any(
        task.get("ansible.builtin.include_tasks") == "heldout-eval.yml"
        for task in _walk(_load_yaml(TASKS / "main.yml"))
    )
    evaluation_entry = heldout_play["tasks"][0]
    assert evaluation_entry["ansible.builtin.include_role"]["tasks_from"] == "heldout-eval.yml"
    assert "laya_systemone_heldout" in evaluation_entry["ansible.builtin.include_role"]["apply"]["tags"]
    assert evaluation_entry["timeout"] == 30
    defaults_entry = heldout_play["pre_tasks"][0]
    assert defaults_entry["timeout"] == 30


def test_heldout_component_checks_health_and_csv_checksum_before_scoring() -> None:
    tasks = _load_yaml(TASKS / "heldout-eval.yml")
    health_index = next(index for index, task in enumerate(tasks) if "health.yml" in json.dumps(task))
    checksum_index = next(
        index for index, task in enumerate(tasks) if task.get("name") == "Verify the held-out CSV checksum"
    )
    command_index = next(
        index for index, task in enumerate(tasks) if task.get("name") == "Run the held-out evaluation on the guest"
    )
    health = _load_yaml(TASKS / "health.yml")[0]

    assert health_index == 1
    assert tasks[0]["name"] == "Validate the held-out component time budget"
    assert health["ansible.builtin.uri"]["url"].endswith("/health")
    assert health["ansible.builtin.uri"]["timeout"] == "{{ laya_systemone_health_timeout_seconds }}"
    assert health["timeout"] == "{{ laya_systemone_health_task_timeout_seconds }}"
    assert checksum_index < command_index


def test_failed_result_upload_removes_the_local_skip_marker() -> None:
    upload = _task(_load_yaml(TASKS / "heldout-eval.yml"), "Upload the held-out evaluation result")
    credential = _task(upload["block"], "Read the writable RustFS credential for held-out result upload")
    assert credential["vars"]["laya_systemone_rustfs_kv_path"] == "{{ laya_systemone_heldout_results_bao_kv_path }}"
    remove, fail = upload["rescue"]
    assert remove["ansible.builtin.file"] == {"path": "{{ laya_systemone_heldout_result_path }}", "state": "absent"}
    assert "ansible.builtin.fail" in fail


def test_bearer_credential_is_hidden_and_eval_command_has_a_total_timeout() -> None:
    task_files = list(TASKS.glob("*.yml"))
    credential_tasks = [
        task
        for path in task_files
        for task in _walk(_load_yaml(path))
        if "block" not in task and "laya_systemone_api_key" in json.dumps(task)
    ]
    command = next(
        task
        for task in _walk(_load_yaml(TASKS / "heldout-eval.yml"))
        if task.get("name") == "Run the held-out evaluation command"
    )

    assert credential_tasks
    assert all(task.get("no_log") is True for task in credential_tasks)
    assert command["async"] == "{{ laya_systemone_heldout_run_timeout_seconds }}"
    assert command["environment"]["LAYA_API_KEY"] == "{{ laya_systemone_api_key }}"
    assert command["become_user"] == "{{ laya_systemone_user }}"
    assert command["no_log"] is True


def test_standalone_nvidia_defaults_entry_does_not_converge_the_guest() -> None:
    defaults_entry = _load_yaml(ROLE.parent / "nvidia_gpu_guest/tasks/defaults-only.yml")

    assert len(defaults_entry) == 1
    assert defaults_entry[0]["ansible.builtin.meta"] == "noop"


def test_shared_rustfs_credential_tasks_are_included_and_secret_output_is_hidden() -> None:
    finetune_dataset_text = (TASKS / "finetune-datasets.yml").read_text(encoding="utf-8")
    assert "community.hashi_vault." not in finetune_dataset_text
    assert "vault_login" not in finetune_dataset_text

    finetune_include = _task(_load_yaml(TASKS / "finetune-datasets.yml"), "Read the scoped RustFS credential for the training datasets")
    heldout_include = _task(_load_yaml(TASKS / "heldout-eval.yml"), "Read the scoped RustFS credential for the held-out CSV")
    assert _included_file(finetune_include) == "rustfs-credential.yml"
    assert _included_file(heldout_include) == "rustfs-credential.yml"
    assert finetune_include["vars"]["laya_systemone_rustfs_kv_path"] == (
        "{{ laya_systemone_finetune_bao_kv_path }}"
    )

    helper_tasks = _load_yaml(TASKS / "rustfs-credential.yml")
    secret_references = (
        "community.hashi_vault.",
        "bao_role_id",
        "bao_secret_id",
        "client_token",
        "_s3_access_key_id",
        "_s3_secret_access_key",
        "_s3_region",
        "_s3_endpoint",
    )
    for task in _walk(helper_tasks):
        own_fields = {key: value for key, value in task.items() if key not in {"block", "rescue", "always"}}
        task_text = json.dumps(own_fields, sort_keys=True).lower()
        if any(reference in task_text for reference in secret_references):
            assert task.get("no_log") is True, task.get("name")


def test_heldout_script_scores_batches_without_writing_row_text(tmp_path: Path) -> None:
    train_csv = tmp_path / "train.csv"
    heldout_csv = tmp_path / "heldout.csv"
    output_path = tmp_path / "result.json"
    train_csv.write_text(
        "text,label\ntrain one,zebra\ntrain two,ant\ntrain three,zebra\n",
        encoding="utf-8",
    )
    fixture_texts = [
        "private heldout alpha",
        "private heldout beta",
        "private heldout gamma",
        "private heldout delta",
    ]
    heldout_csv.write_text(
        "text,label\n"
        "private heldout alpha,ant\n"
        "private heldout beta,zebra\n"
        "private heldout gamma,ant\n"
        "private heldout delta,zebra\n",
        encoding="utf-8",
    )

    requests: list[tuple[str | None, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request_size = int(self.headers["Content-Length"])
            request_body = json.loads(self.rfile.read(request_size))
            requests.append((self.headers.get("Authorization"), request_body))
            results = []
            for state in request_body["states"]:
                if state == fixture_texts[0]:
                    answer = {"type": "choice", "choice": "ant", "answer_confidence": 0.9}
                elif state == fixture_texts[1]:
                    answer = {"type": "choice", "choice": "ant", "answer_confidence": 0.8}
                elif state == fixture_texts[2]:
                    answer = {"type": "choice"}
                else:
                    answer = {"type": "choice", "choice": "zebra", "answer_confidence": 0.6}
                results.append({"answers": {"label": answer}})

            response = json.dumps({"results": results, "total_usage": {"input_tokens": 0, "output_tokens": 0}})
            encoded = response.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:  # silence the default stderr access log
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {**os.environ, "LAYA_API_KEY": "test-heldout-key"}
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--csv",
                str(heldout_csv),
                "--train-csv",
                str(train_csv),
                "--url",
                f"http://127.0.0.1:{server.server_port}/v1/systemone/batch",
                "--model",
                "system1-v1",
                "--model",
                "english",
                "--batch-size",
                "2",
                "--out",
                str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    assert len(requests) == 4
    assert all(auth == "Bearer test-heldout-key" for auth, _ in requests)
    assert all(len(body["states"]) <= 2 for _, body in requests)
    assert all(body["questions"]["label"]["criteria"] == ["zebra", "ant"] for _, body in requests)

    result_text = output_path.read_text(encoding="utf-8")
    result = json.loads(result_text)
    assert result["heldout_sha256"] == hashlib.sha256(heldout_csv.read_bytes()).hexdigest()
    assert result["labels"] == ["zebra", "ant"]
    assert result["n"] == 4
    for metrics in result["models"].values():
        assert metrics["accuracy"] == 0.5
        assert metrics["correct"] == 2
        assert metrics["unanswered"] == 1
        assert metrics["mean_answer_confidence"] == 0.7667
        assert metrics["per_class"] == {
            "ant": {"n": 2, "correct": 1},
            "zebra": {"n": 2, "correct": 1},
        }

    assert '"text"' not in result_text
    assert all(text not in result_text for text in fixture_texts)
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600


def test_heldout_timeout_writes_only_a_private_sanitized_error_line(tmp_path: Path) -> None:
    heldout_csv = tmp_path / "heldout.csv"
    output_path = tmp_path / "result.json"
    secret_key = "test-heldout-key"
    private_row = "private timeout fixture row"
    heldout_csv.write_text(f"text,label\n{private_row},ant\nsecond row,zebra\n", encoding="utf-8")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(True)
            time.sleep(0.2)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"results": []}')
            except OSError:
                pass

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {**os.environ, "LAYA_API_KEY": secret_key}
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--csv",
                str(heldout_csv),
                "--url",
                f"http://127.0.0.1:{server.server_port}/v1/systemone/batch",
                "--model",
                "english",
                "--request-timeout",
                "0.05",
                "--out",
                str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    error_path = Path(f"{output_path}.error")
    error_text = error_path.read_text(encoding="utf-8")
    assert completed.returncode == 1
    assert len(requests) == 1
    assert completed.stderr == "held-out evaluation failed: Laya batch request timed out\n"
    assert error_text == completed.stderr
    assert len(error_text.splitlines()) == 1
    assert secret_key not in error_text
    assert private_row not in error_text
    assert stat.S_IMODE(error_path.stat().st_mode) == 0o600
    assert error_path.stat().st_uid == os.geteuid()
    assert not output_path.exists()
