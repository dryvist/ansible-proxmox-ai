"""Shared helpers for the Dify agent DSL contract tests."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
AGENT_DSLS = ROOT / "roles/dify_docker/vars/agent_dsls.yml"
IMPORT_LOOP = ROOT / "roles/dify_docker/tasks/import_agent_dsls.yml"
IMPORT_ONE = ROOT / "roles/dify_docker/tasks/import_agent_dsl.yml"
LIST_CATALOG_PAGE = ROOT / "roles/dify_docker/tasks/list_catalog_app_page.yml"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _expanded_yaml(path: Path) -> list[dict]:
    tasks = []
    for task in _yaml(path):
        include = task.get("ansible.builtin.include_tasks")
        if include in {
            "import_agent_dsl_fetch.yml",
            "import_agent_dsl_reconcile.yml",
            "import_agent_dsl_record.yml",
        }:
            tasks.extend(_expanded_yaml(path.parent / include))
        else:
            tasks.append(task)
    return tasks


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)
