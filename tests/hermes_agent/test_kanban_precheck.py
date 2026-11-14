"""The Hermes Kanban precheck accepts the resolver path and reports stray copies."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TASKS_FILE = ROOT / "roles" / "hermes_agent" / "tasks" / "mcp_precheck.yml"
TASK_NAMES = (
    "Find Hermes Kanban database files under the shared home",
    "Warn about Hermes Kanban database copies outside the resolver path",
    "Assert an existing Hermes Kanban database is at the resolver path",
)


def _run_precheck(
    tmp_path: Path, relative_files: tuple[str, ...]
) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "hermes-home"
    home.mkdir(parents=True, exist_ok=True)
    for relative_path in relative_files:
        path = home / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    tasks = yaml.safe_load(TASKS_FILE.read_text(encoding="utf-8"))
    selected = [task for task in tasks if task.get("name") in TASK_NAMES]
    assert tuple(task["name"] for task in selected) == TASK_NAMES

    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        yaml.safe_dump(
            {"all": {"hosts": {"localhost": {"ansible_connection": "local"}}}}
        ),
        encoding="utf-8",
    )
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Exercise the Hermes Kanban precheck",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {"hermes_agent_hermes_home": str(home)},
                    "tasks": selected,
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            "ansible-playbook",
            "--inventory",
            str(inventory),
            "--connection",
            "local",
            str(playbook),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_canonical_kanban_database_passes(tmp_path: Path) -> None:
    result = _run_precheck(tmp_path, ("kanban.db",))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNING:" not in result.stdout + result.stderr


def test_canonical_database_with_stray_copy_passes_and_warns_with_path(
    tmp_path: Path,
) -> None:
    stray_path = tmp_path / "hermes-home" / "profiles" / "orchestrator" / "kanban.db"
    result = _run_precheck(tmp_path, ("kanban.db", "profiles/orchestrator/kanban.db"))
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "WARNING:" in output
    assert str(stray_path) in output


def test_stray_only_database_fails_for_missing_resolver_path(tmp_path: Path) -> None:
    result = _run_precheck(tmp_path, ("profiles/orchestrator/kanban.db",))
    output = result.stdout + result.stderr
    stray_path = tmp_path / "hermes-home" / "profiles" / "orchestrator" / "kanban.db"

    assert result.returncode != 0, output
    assert "WARNING:" in output
    assert str(stray_path) in output


def test_empty_new_home_passes_before_first_gateway_start(tmp_path: Path) -> None:
    result = _run_precheck(tmp_path, ())

    assert result.returncode == 0, result.stdout + result.stderr
