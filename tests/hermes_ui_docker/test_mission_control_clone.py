"""The mission-control clone stays idempotent on ansible-core 2.22."""

from __future__ import annotations

from pathlib import Path

import yaml

TASKS = Path(__file__).resolve().parents[2] / "roles/hermes_ui_docker/tasks/main.yml"


def test_mission_control_clone_is_not_shallow() -> None:
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    clone = next(task for task in tasks if task.get("name") == "Clone mission-control at the pinned tag")
    args = clone["ansible.builtin.git"]
    # A shallow tag clone has no origin/HEAD, and ansible-core 2.22's git module fails on the next run.
    assert "depth" not in args
    assert args["version"] == "{{ hermes_ui_docker_mission_control_ref }}"
