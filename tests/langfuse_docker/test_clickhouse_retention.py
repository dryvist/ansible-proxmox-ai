"""Keep the ClickHouse trace retention window and table list in the role contract."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles/langfuse_docker"

VOLUME_GIB = 80
STEADY_STATE_FRACTION = 0.6
FILL_GIB_PER_DAY = 14


def _defaults():
    return yaml.safe_load((ROLE / "defaults/main.yml").read_text(encoding="utf-8"))


def test_retention_days_is_the_longest_window_under_the_steady_state_target():
    days = _defaults()["langfuse_docker_clickhouse_retention_days"]
    target_gib = VOLUME_GIB * STEADY_STATE_FRACTION

    assert days * FILL_GIB_PER_DAY <= target_gib < (days + 1) * FILL_GIB_PER_DAY


def test_retention_covers_every_trace_table_with_its_timestamp_column():
    tables = {
        entry["name"]: entry["column"]
        for entry in _defaults()["langfuse_docker_clickhouse_retention_tables"]
    }

    assert tables == {
        "traces": "timestamp",
        "observations": "start_time",
        "scores": "timestamp",
        "events_full": "start_time",
        "events_core": "start_time",
    }
