"""Check the LLM conversation schema rendered for the ClickHouse role."""

import re
from pathlib import Path

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/clickhouse_docker"
TEMPLATE = ROLE / "templates/llm_schema.sql.j2"
EXPECTED_TABLES = [
    "claude",
    "codex",
    "opencode",
    "zcode",
    "gemini",
    "antigravity",
    "local_light",
    "local_cpu_9b",
    "local_gpu",
    "local_large",
    "local_vllm",
]
EXPECTED_COLUMNS = [
    "event_time DateTime64(3)",
    "ingest_time DateTime64(3) DEFAULT now64(3)",
    "ingest LowCardinality(String) DEFAULT 'live'",
    "session_id String",
    "message_id String",
    "role LowCardinality(String)",
    "model String",
    "repo String",
    "cwd String",
    "tool_name LowCardinality(String)",
    "content String CODEC(ZSTD(3))",
    "raw String CODEC(ZSTD(3))",
    "host String",
]


def _defaults() -> dict:
    return yaml.safe_load((ROLE / "defaults/main.yml").read_text(encoding="utf-8"))


def _render() -> str:
    defaults = _defaults()
    return jinja2.Template(TEMPLATE.read_text(encoding="utf-8")).render(
        clickhouse_docker_llm_database=defaults["clickhouse_docker_llm_database"],
        clickhouse_docker_llm_tables=defaults["clickhouse_docker_llm_tables"],
    )


def _statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


def test_llm_tables_list_matches_the_agent_sources():
    assert _defaults()["clickhouse_docker_llm_tables"] == EXPECTED_TABLES


def test_database_and_one_table_per_source_are_created_idempotently():
    statements = _statements(_render())

    assert statements[0] == "CREATE DATABASE IF NOT EXISTS llm"
    tables = [s for s in statements if s.startswith("CREATE TABLE")]
    assert len(tables) == len(EXPECTED_TABLES)
    for table, statement in zip(EXPECTED_TABLES, tables):
        assert statement.startswith(f"CREATE TABLE IF NOT EXISTS llm.{table}\n")


def test_every_table_has_the_same_columns_in_the_same_order():
    tables = [s for s in _statements(_render()) if s.startswith("CREATE TABLE")]

    for statement in tables:
        body = statement[statement.index("(\n") + 2 : statement.index("\n)\n")]
        columns = [line.strip().rstrip(",") for line in body.splitlines()]
        assert columns == EXPECTED_COLUMNS


def test_every_table_is_replacing_on_message_id_partitioned_by_month_without_ttl():
    tables = [s for s in _statements(_render()) if s.startswith("CREATE TABLE")]

    for statement in tables:
        assert "ENGINE = ReplacingMergeTree(ingest_time)" in statement
        assert "PARTITION BY toYYYYMM(event_time)" in statement
        assert "ORDER BY (session_id, event_time, message_id)" in statement
        assert not re.search(r"\bTTL\b", statement)
