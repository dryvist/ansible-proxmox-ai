from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVING_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving.yml"
RESTART_TASKS = REPO_ROOT / "playbooks/tasks/llm_router_rolling_restart.yml"


def test_router_render_is_parallel_and_only_pending_restarts_roll() -> None:
    plays = yaml.safe_load(SERVING_PLAYBOOK.read_text(encoding="utf-8"))
    render = next(
        play
        for play in plays
        if play.get("name") == "Render the LiteLLM router config (all members in parallel)"
    )
    restart = next(
        play
        for play in plays
        if play.get("name") == "Roll LiteLLM restarts (one member at a time)"
    )
    restart_import = next(
        task
        for task in restart["tasks"]
        if task.get("name") == "Restart pending members and verify every member"
    )
    assert restart_import["ansible.builtin.import_tasks"] == "tasks/llm_router_rolling_restart.yml"
    restart_block = next(
        task
        for task in yaml.safe_load(RESTART_TASKS.read_text(encoding="utf-8"))
        if task.get("name") == "Restart pending members and verify every member"
    )
    steps = {task["name"]: task for task in restart_block["block"]}

    assert render["hosts"] == "llm_router_group"
    assert "serial" not in render
    assert render["any_errors_fatal"] is True
    assert restart["hosts"] == "llm_router_group"
    assert restart["serial"] == 1
    assert "when" not in restart_block
    assert "llm_router_restart_pending" in steps["Restart litellm"]["when"]
    assert "when" not in steps["Verify the rendered router runtime"]
