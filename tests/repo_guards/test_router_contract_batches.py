"""Exercise the native workflow loop with recorded CLI processes and failures."""

import hashlib
import json
import os
import runpy
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/_llm-router-contract.yml").read_text())
JOB = WORKFLOW["jobs"]["llm-router-contract"]
SELECTOR = runpy.run_path(str(ROOT / ".github/scripts/select-contract-scope.py"))
BATCHES = [{"batch": index, "playbooks": " ".join(paths)}
           for index, paths in enumerate(SELECTOR["llm_router_matrix"]())]
LOOP = JOB["steps"][-1]["run"]


def _replay(tmp_path, batch, failure=""):
    recorded = tmp_path / "calls.jsonl"
    binary = tmp_path / "ansible-playbook"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['RECORDED_CALLS'], 'a') as log:\n"
        "    log.write(json.dumps({'pid': os.getpid(), 'argv': sys.argv[1:]}) + '\\n')\n"
        "print('fixture CLI result for', sys.argv[1])\n"
        "sys.exit(17 if sys.argv[1] == os.environ['FAILING_PLAYBOOK'] else 0)\n"
    )
    binary.chmod(0o755)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", LOOP], cwd=ROOT,
        env=os.environ | {
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "PLAYBOOKS": batch["playbooks"], "RECORDED_CALLS": str(recorded),
            "FAILING_PLAYBOOK": failure,
        },
        capture_output=True, text=True, timeout=30,
    )
    calls = [json.loads(line) for line in recorded.read_text().splitlines()]
    return result, calls


def _assert_calls(result, calls, batch):
    paths = batch["playbooks"].split()
    assert [call["argv"] for call in calls] == [
        [path, "-i", "localhost,", "-c", "local"] for path in paths
    ]
    assert len({call["pid"] for call in calls}) == len(paths)
    assert result.stdout.count("::group::") == len(paths)
    assert result.stdout.count("::endgroup::") == len(paths)
    assert result.stdout.count("fixture CLI result for") == len(paths)


def test_matrix_paths_are_nonempty_unique_existing_playbooks():
    paths = [path for batch in BATCHES for path in batch["playbooks"].split()]
    assert all(batch["playbooks"].strip() for batch in BATCHES)
    assert len(BATCHES) == 98
    assert all(len(batch["playbooks"].split()) <= 3 for batch in BATCHES)
    assert len(paths) == len(set(paths)) == 98
    assert hashlib.sha256("\n".join(sorted(paths)).encode()).hexdigest() == (
        "917dcb81eb7b2dae2a371998f721b86c234af84bfc76519f96b2cdba04b985b6"
    )
    assert JOB["strategy"]["matrix"]["playbook"] == "${{ fromJSON(inputs.playbooks) }}"
    assert all((ROOT / path).is_file() for path in paths)
    assert JOB["strategy"]["fail-fast"] is False
    assert JOB["strategy"]["max-parallel"] == 32


@pytest.mark.parametrize("batch", BATCHES, ids=lambda batch: str(batch["batch"]))
def test_every_contract_executes_its_path_in_an_independent_cli_process(tmp_path, batch):
    result, calls = _replay(tmp_path, batch)
    assert result.returncode == 0, result.stderr
    _assert_calls(result, calls, batch)


@pytest.mark.parametrize("position", ["early", "middle", "late"])
def test_any_failure_reaches_the_gate_after_all_paths_run(tmp_path, position):
    sample = [BATCHES[0], BATCHES[len(BATCHES) // 2], BATCHES[-1]]
    batch = {"batch": "failure-propagation",
             "playbooks": " ".join(item["playbooks"] for item in sample)}
    paths = batch["playbooks"].split()
    index = {"early": 0, "middle": len(paths) // 2, "late": len(paths) - 1}[position]
    result, calls = _replay(tmp_path, batch, failure=paths[index])
    assert result.returncode != 0
    _assert_calls(result, calls, batch)


def _is_playbook(path: Path) -> bool:
    doc = yaml.safe_load(path.read_text())
    return isinstance(doc, list) and any(
        isinstance(item, dict) and {"hosts", "import_playbook", "ansible.builtin.import_playbook"} & set(item)
        for item in doc)


def test_every_router_playbook_is_listed_or_imported_by_a_listed_one():
    listed = {path for batch in BATCHES for path in batch["playbooks"].split()}
    imported = set()
    for path in listed:
        for item in yaml.safe_load((ROOT / path).read_text()):
            for key in ("import_playbook", "ansible.builtin.import_playbook"):
                if isinstance(item, dict) and key in item:
                    imported.add((Path(path).parent / item[key]).as_posix())
    playbooks = {path.relative_to(ROOT).as_posix()
                 for path in (ROOT / "tests/llm_router").glob("*.yml") if _is_playbook(path)}
    assert playbooks - listed - imported == set()
