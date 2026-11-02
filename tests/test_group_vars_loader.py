from pathlib import Path

from _group_vars import load_group_vars_all


def test_load_group_vars_all_merges_each_top_level_file() -> None:
    group_vars = load_group_vars_all()

    assert "ai_agent_default_concurrency" in group_vars
    assert "agentgateway_docker_a2a_allowed_tier" in group_vars


def test_later_file_wins(tmp_path: Path) -> None:
    (tmp_path / "00-first.yml").write_text("value: first\n", encoding="utf-8")
    (tmp_path / "01-second.yml").write_text("value: second\n", encoding="utf-8")

    assert load_group_vars_all(tmp_path) == {"value": "second"}
