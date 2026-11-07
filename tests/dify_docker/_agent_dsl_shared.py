"""Shared helpers for the Dify agent DSL contract tests."""

from pathlib import Path

import yaml


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)
