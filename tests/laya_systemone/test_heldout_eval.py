"""Contracts and local HTTP behavior for Laya held-out evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles/laya_systemone"
TASKS = ROLE / "tasks"
SCRIPT = ROLE / "files/heldout_eval.py"


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


def test_heldout_defaults_are_opt_in_and_use_separate_read_and_write_paths() -> None:
    defaults = _load_yaml(ROLE / "defaults/main/30-heldout.yml")
    finetune_defaults = _load_yaml(ROLE / "defaults/main/20-finetune.yml")

    assert defaults["laya_systemone_heldout_enabled"] is False
    assert defaults["laya_systemone_heldout_results_bao_kv_path"] == "rustfs/system1/rw"
    assert finetune_defaults["laya_systemone_finetune_bao_kv_path"] == "rustfs/system1/ro"
    assert defaults["laya_systemone_heldout_batch_size"] == 16


def test_heldout_include_follows_health_and_is_opt_in() -> None:
    main = _load_yaml(TASKS / "main.yml")
    health_index = next(
        index for index, task in enumerate(main) if task.get("name") == "Wait for Laya health to return successfully"
    )
    heldout_index = next(
        index for index, task in enumerate(main) if task.get("name") == "Optionally score Laya models on held-out data"
    )
    include = main[heldout_index]

    assert heldout_index == health_index + 1
    assert _included_file(include) == "heldout-eval.yml"
    assert include["when"] == ["laya_systemone_heldout_enabled | bool", "not ansible_check_mode"]
    assert "laya_systemone_heldout" in include["tags"]
    assert "laya_systemone_heldout" in include["ansible.builtin.include_tasks"]["apply"]["tags"]


def test_failed_result_upload_removes_the_local_skip_marker() -> None:
    upload = _task(_load_yaml(TASKS / "heldout-eval.yml"), "Upload the held-out evaluation result")
    credential = _task(upload["block"], "Read the writable RustFS credential for held-out result upload")
    assert credential["vars"]["laya_systemone_rustfs_kv_path"] == "{{ laya_systemone_heldout_results_bao_kv_path }}"
    remove, fail = upload["rescue"]
    assert remove["ansible.builtin.file"] == {"path": "{{ laya_systemone_heldout_result_path }}", "state": "absent"}
    assert "ansible.builtin.fail" in fail


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
