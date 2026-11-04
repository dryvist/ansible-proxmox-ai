"""Render the wall feed SQL and pick out its statements and per-table branches."""

import re
from pathlib import Path

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/clickhouse_docker"
WALL_TABLES = ["claude", "codex", "antigravity", "gemini", "zcode", "opencode"]
WALL_MODES = ("stopwords", "views", "explain")
WORD_CLOUD = "CREATE MATERIALIZED VIEW IF NOT EXISTS llm.word_cloud"
RECENT = "CREATE MATERIALIZED VIEW IF NOT EXISTS llm.recent_activity"


def _defaults() -> dict:
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text(encoding="utf-8"))
    # tasks/wall_feed.yml passes the stopword file in as this list; mirror that here.
    stopwords = (ROLE / "files" / defaults["clickhouse_docker_stopwords_file"]).read_text(encoding="utf-8")
    defaults["clickhouse_docker_stopwords"] = [w.strip() for w in stopwords.splitlines() if w.strip()]
    return defaults


def _render(template: str, **variables) -> str:
    return jinja2.Template((ROLE / "templates" / template).read_text(encoding="utf-8")).render(
        **{**_defaults(), **variables}
    )


def _statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.strip().rstrip(";").split(";\n") if part.strip()]


def _all_statements() -> list[str]:
    return [
        statement
        for mode in WALL_MODES
        for statement in _statements(_render("llm_wall.sql.j2", clickhouse_docker_wall_mode=mode))
    ]


def _statement(prefix: str) -> str:
    return next(s for s in _all_statements() if s.startswith(prefix))


def _branch(statement: str, table: str) -> str:
    # Each harness is one UNION ALL branch that reads its own table.
    return next(b for b in statement.split("UNION ALL") if f"FROM llm.{table} FINAL" in b)


def _message_rows(branch: str) -> str:
    # The derivation shared by both views: the rows of one table, whitespace-normalised.
    match = re.search(r"SELECT event_time, msg_role.*?msg_role IN \('user', 'assistant'\)", branch, re.S)
    return " ".join(match.group(0).split())
