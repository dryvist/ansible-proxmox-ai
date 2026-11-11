"""The tau2 preflight gate really fails the play, not just exists.

Runs the real playbooks/tasks/benchmark-run/preflight.yml through
ansible-playbook against an offline survey fixture, so a missing
api_key_kv_path is proven to fail the validation play (and a complete tau2
survey is proven to pass it) rather than assumed to. The fixture reuses the
`_benchmark_run_*` pins parsed from playbooks/benchmark-run.yml itself, so a
pin bump cannot silently drift the fixture away from the play.

Run (from the REPO ROOT):
    pytest tests/benchmark_run/test_tau2_preflight_gate.py
"""

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
PLAYBOOK = ROOT / "playbooks/benchmark-run.yml"
PREFLIGHT = ROOT / "playbooks/tasks/benchmark-run/preflight.yml"
ENGINE_DEFAULTS = ROOT / "roles/vllm_serving/defaults/main/00-engine.yml"


def _fixture_play(tmp_path: Path, survey: dict[str, object]) -> Path:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    fixture = {
        "name": "Preflight tau2 survey fixture",
        "hosts": "localhost",
        "gather_facts": False,
        "vars_files": [str(ENGINE_DEFAULTS)],
        "vars": {
            **plays[1]["vars"],
            "node": "tau2-fixture-node",
            "client_vmid": "100",
            "gpu_guest_vmid": "200",
            "engine_url": "http://tau2-fixture-engine.example/v1",
            "model": "tau2-fixture-model",
            "tokenizer": "tau2-fixture-tokenizer",
            "suite": "tau2-banking-knowledge",
            "concurrency": 4,
            "run_id": "tau2-fixture-run-1",
            "tofu_data": {
                "nodes": {"tau2-fixture-node": {"role": "tau2-fixture-role"}},
                "domain": "fixture.example",
            },
            **survey,
        },
        "tasks": [
            {"name": "Run the real benchmark-run preflight",
             "ansible.builtin.import_tasks": str(PREFLIGHT)},
        ],
    }
    path = tmp_path / "tau2_preflight_fixture.yml"
    path.write_text(yaml.safe_dump([fixture]), encoding="utf-8")
    return path


def _run_preflight(tmp_path: Path, survey: dict[str, object]) -> subprocess.CompletedProcess:
    env = {**os.environ, "PROXMOX_SSH_KEY_PATH": str(tmp_path / "fixture-key")}
    # cwd outside the repo so ansible.cfg (and its collection callbacks) is
    # not loaded; the preflight uses builtin modules only.
    return subprocess.run(
        ["ansible-playbook", "-i", "localhost,", "-c", "local", str(_fixture_play(tmp_path, survey))],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        check=False,
        timeout=180,
    )


def test_tau2_survey_without_api_key_kv_path_fails_preflight(tmp_path: Path) -> None:
    result = _run_preflight(tmp_path, {})
    assert result.returncode != 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "api_key_kv_path" in combined
    assert "api_key_kv_field" in combined


def test_complete_tau2_survey_passes_preflight(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        {"api_key_kv_path": "ai/tau2", "api_key_kv_field": "OPENAI_API_KEY"},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_tau2_survey_with_unknown_domain_fails_preflight(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        {"api_key_kv_path": "ai/tau2", "api_key_kv_field": "OPENAI_API_KEY", "tau2_domain": "finance"},
    )
    assert result.returncode != 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "tau2_domain" in combined


def test_tau2_survey_with_zero_trials_fails_preflight(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        {"api_key_kv_path": "ai/tau2", "api_key_kv_field": "OPENAI_API_KEY", "tau2_num_trials": 0},
    )
    assert result.returncode != 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "tau2_num_trials" in combined


def test_tau2_survey_with_airline_domain_passes_preflight(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        {"api_key_kv_path": "ai/tau2", "api_key_kv_field": "OPENAI_API_KEY", "tau2_domain": "airline"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
