from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVING_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving.yml"


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
    restart_block = next(
        task
        for task in restart["tasks"]
        if task.get("name") == "Restart and verify the rendered config on pending members"
    )

    assert render["hosts"] == "llm_router_group"
    assert "serial" not in render
    assert render["any_errors_fatal"] is True
    assert restart["hosts"] == "llm_router_group"
    assert restart["serial"] == 1
    assert "llm_router_restart_pending" in restart_block["when"]
