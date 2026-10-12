"""Exercise the sensitivity guard with a complete declared-key fixture."""

import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
KEY_MARKER = "sk-fixture-sensitivity-guard-output-marker"


@pytest.mark.parametrize(
    "sensitivity_tag, expected_failure",
    [
        (None, False),
        ("&sensitivity:public", False),
        ("&sensitivity:sensitive", False),
        ("&sensitivity:pii", False),
        ("invalid-fixture-tag", True),
    ],
)
def test_sensitivity_guard_output(tmp_path, sensitivity_tag, expected_failure):
    tasks = yaml.safe_load(
        (REPO_ROOT / "roles/llm_router/tasks/seed-keys.yml").read_text(encoding="utf-8")
    )
    guard = next(
        task for task in tasks
        if task.get("name") == "Validate every key's declared sensitivity class before key reads or writes"
    )
    shape = json.loads(
        (REPO_ROOT / "tests/llm_router/fixtures/seed-key-response-shape.json")
        .read_text(encoding="utf-8")
    )
    # Declared keys carry metadata with the captured response's field types.
    # Every value below is synthetic, including the credential output marker.
    metadata = {
        field: ["fixture-operator-tag"] if isinstance(field_shape, dict) else "fixture-attribution"
        for field, field_shape in shape["metadata"].items()
    }
    key = {
        "alias": "fixture-router-caller",
        "value": KEY_MARKER,
        "models": ["fixture-model"],
        "models_authoritative": True,
        "daily_budget": 0,
        "metadata": metadata,
        "object_permission": {"mcp_servers": ["no-mcp-servers"]},
    }
    if sensitivity_tag is not None:
        key["sensitivity_tag"] = sensitivity_tag
    playbook = tmp_path / "guard.yml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Verify sensitivity admission",
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {"llm_router_virtual_keys": [key]},
        "tasks": [guard],
    }], sort_keys=False), encoding="utf-8")
    result = subprocess.run(
        ["ansible-playbook", str(playbook), "-i", "localhost,", "-c", "local"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-tmp")},
    )
    assert (result.returncode != 0) == expected_failure, result.stdout + result.stderr
    assert KEY_MARKER not in result.stdout + result.stderr
    if expected_failure:
        assert "censored" in result.stdout
