"""Check the wall feed: stopwords, refreshable views, HTTP handlers and wall_reader."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/clickhouse_docker"
WALL_TABLES = ["claude", "codex", "antigravity", "gemini", "zcode", "opencode"]
YAML_11_SCALARS = ["n", "no", "null", "off", "on", "y", "yes"]


def _defaults() -> dict:
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text(encoding="utf-8"))
    # tasks/wall_feed.yml passes the stopword file in as this list; mirror that here.
    stopwords = (ROLE / "files" / defaults["clickhouse_docker_stopwords_file"]).read_text(encoding="utf-8")
    defaults["clickhouse_docker_stopwords"] = [w.strip() for w in stopwords.splitlines() if w.strip()]
    return defaults


def _render(template: str) -> str:
    return jinja2.Template((ROLE / "templates" / template).read_text(encoding="utf-8")).render(
        **_defaults()
    )


def _statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.strip().rstrip(";").split(";\n") if part.strip()]


def _statement(prefix: str) -> str:
    return next(s for s in _statements(_render("llm_wall.sql.j2")) if s.startswith(prefix))


def _without_literals(sql: str) -> str:
    return re.sub(r"'(?:\\.|[^'\\])*'", "''", sql)


def test_wall_sources_are_llm_tables_and_cover_every_harness():
    defaults = _defaults()
    sources = defaults["clickhouse_docker_wall_sources"]

    assert [source["table"] for source in sources] == WALL_TABLES
    assert set(WALL_TABLES) <= set(defaults["clickhouse_docker_llm_tables"])
    for source in sources:
        assert source["content"] in ("text", "blocks")
        assert source["output_roles"]
        assert "user" not in source["output_roles"]


def test_stopwords_are_strings_and_include_yaml_11_keywords():
    defaults = _defaults()
    words = defaults["clickhouse_docker_stopwords"] + defaults["clickhouse_docker_stopwords_extra"]

    assert all(isinstance(word, str) and word for word in words)
    assert len(defaults["clickhouse_docker_stopwords"]) > 1000
    for word in YAML_11_SCALARS + ["the", "and", "doesn't"]:
        assert word in defaults["clickhouse_docker_stopwords"]
    assert not set(defaults["clickhouse_docker_stopwords_extra"]) & set(
        defaults["clickhouse_docker_stopwords"]
    )


def test_rendered_wall_sql_has_three_statements_with_balanced_quotes_and_parens():
    statements = _statements(_render("llm_wall.sql.j2"))

    assert [s.split("\n")[0] for s in statements] == [
        "CREATE OR REPLACE TABLE llm.stopwords",
        "CREATE MATERIALIZED VIEW IF NOT EXISTS llm.word_cloud",
        "CREATE MATERIALIZED VIEW IF NOT EXISTS llm.recent_activity",
    ]
    for statement in statements:
        bare = _without_literals(statement)
        assert "'" not in bare.replace("''", "")
        assert bare.count("(") == bare.count(")")
        assert "{{" not in statement and "{%" not in statement


def test_stopwords_table_is_rebuilt_from_every_default_word_through_the_tokeniser():
    statement = _statement("CREATE OR REPLACE TABLE llm.stopwords")
    defaults = _defaults()

    assert "(\n    word String\n)" in statement
    assert "splitByNonAlpha(lower(w))" in statement
    assert "'doesn\\'t'" in statement
    for word in defaults["clickhouse_docker_stopwords"] + defaults["clickhouse_docker_stopwords_extra"]:
        assert "'" + word.replace("\\", "\\\\").replace("'", "\\'") + "'" in statement


def test_word_cloud_refreshes_hourly_over_every_harness_and_keeps_top_words_per_source():
    statement = _statement("CREATE MATERIALIZED VIEW IF NOT EXISTS llm.word_cloud")

    assert "REFRESH EVERY 1 HOUR\n(\n    source String,\n    word String,\n    n UInt64\n)" in statement
    assert statement.count("UNION ALL") == len(WALL_TABLES) - 1
    for table in WALL_TABLES:
        assert f"FROM llm.{table} FINAL" in statement
        assert f"if(role = 'user', 'user', '{table}') AS source" in statement
    assert "ARRAY JOIN splitByNonAlpha(lower(body)) AS word" in statement
    assert "lengthUTF8(word) BETWEEN 3 AND 24" in statement
    assert "word NOT IN (SELECT word FROM llm.stopwords)" in statement
    assert statement.endswith("LIMIT 200 BY source")


def test_word_cloud_reads_only_text_blocks_and_each_harness_output_role():
    statement = _statement("CREATE MATERIALIZED VIEW IF NOT EXISTS llm.word_cloud")
    branches = statement.split("UNION ALL")

    claude, codex, gemini = branches[0], branches[1], branches[3]
    assert "JSONExtractString(b, 'type') IN ('', 'text')" in claude
    assert "NOT JSONExtractBool(b, 'thought')" in claude
    assert "JSONExtractArrayRaw(content)" in claude
    assert "WHERE role IN ('user', 'assistant')" in claude
    assert "JSONExtractArrayRaw" not in codex
    assert "WHERE role IN ('user', 'gemini', 'model')" in gemini
    assert "match(content, '^[[:space:]]*(<[a-z_-]+>|# AGENTS\\\\.md instructions|" in codex


def test_recent_activity_refreshes_every_minute_and_never_returns_content():
    statement = _statement("CREATE MATERIALIZED VIEW IF NOT EXISTS llm.recent_activity")
    outer = statement.split("FROM\n(")[0]

    assert (
        "REFRESH EVERY 1 MINUTE\n(\n    ts DateTime,\n    source String,\n    model String,\n"
        "    role String,\n    chars UInt32\n)"
    ) in statement
    assert "content" not in outer
    assert "toUInt32(lengthUTF8(content)) AS chars" in statement
    assert statement.count("UNION ALL") == len(WALL_TABLES) - 1
    for table in WALL_TABLES:
        assert f"FROM llm.{table} FINAL" in statement
    assert statement.endswith("ORDER BY ts DESC\nLIMIT 40")


def test_wall_handlers_serve_two_fixed_get_queries_and_keep_the_defaults():
    root = ET.fromstring(_render("config.d/wall-handlers.xml.j2").encode("utf-8"))
    handlers = root.find("http_handlers")
    rules = handlers.findall("rule")

    assert handlers.find("defaults") is not None
    assert [rule.findtext("url") for rule in rules] == ["/wall/words", "/wall/recent"]
    for rule in rules:
        assert rule.findtext("methods") == "GET"
        assert rule.findtext("handler/type") == "predefined_query_handler"
        assert rule.findtext("handler/query").endswith(" FORMAT JSON")
    assert rules[0].findtext("handler/query").startswith("SELECT source, word, n FROM llm.word_cloud ")
    assert rules[1].findtext("handler/query") == (
        "SELECT ts, source, model, role, chars FROM llm.recent_activity "
        "ORDER BY ts DESC LIMIT 40 FORMAT JSON"
    )


def test_wall_handlers_are_deployed_and_mounted_read_only():
    tasks = (ROLE / "tasks/main.yml").read_text(encoding="utf-8")
    compose = (ROLE / "templates/docker-compose.yml.j2").read_text(encoding="utf-8")

    assert "src: config.d/wall-handlers.xml.j2" in tasks
    assert 'dest: "{{ clickhouse_docker_config_d_dir }}/wall_handlers.xml"' in tasks
    assert (
        "{{ clickhouse_docker_config_d_dir }}/wall_handlers.xml"
        ":/etc/clickhouse-server/config.d/wall_handlers.xml:ro"
    ) in compose
    wall_tasks = (ROLE / "tasks/wall_feed.yml").read_text(encoding="utf-8")
    assert "ansible.builtin.include_tasks: wall_feed.yml" in tasks
    assert "lookup('ansible.builtin.template', 'llm_wall.sql.j2')" in wall_tasks


def test_wall_reader_password_comes_only_from_openbao():
    password = _defaults()["clickhouse_docker_wall_reader_password"]

    assert "bao_clickhouse_secrets" in password
    assert ".WALL_READER_PASSWORD" in password
    assert "mandatory(" in password
    assert "lookup(" not in password
    domains = (ROOT / "inventory/group_vars/all/openbao_secrets/10-domains-identities.yml").read_text(
        encoding="utf-8"
    )
    assert "- ai/clickhouse-wall  # WALL_READER_PASSWORD" in domains


def test_wall_reader_is_readonly_limited_and_granted_only_the_two_views():
    defaults = _defaults()
    tasks = (ROLE / "tasks/wall_feed.yml").read_text(encoding="utf-8")
    task = tasks.split("- name: Provision the wall read-only ClickHouse user")[1].split("- name:")[0]
    settings = jinja2.Template(
        "{{ clickhouse_docker_wall_reader_settings.items() | map('join', ' = ') | join(', ') }}"
    ).render(**defaults)

    assert defaults["clickhouse_docker_wall_reader_user"] == "wall_reader"
    assert settings == (
        "readonly = 1, max_result_rows = 5000, max_execution_time = 5, max_memory_usage = 268435456"
    )
    assert "SETTINGS {{ clickhouse_docker_wall_reader_settings.items()" in task
    assert task.count("GRANT SELECT ON") == 2
    assert (
        "GRANT SELECT ON {{ clickhouse_docker_llm_database }}.{{ clickhouse_docker_wall_words_view }}\n"
        "               TO {{ clickhouse_docker_wall_reader_user }} WITH REPLACE OPTION;"
    ) in task
    assert (
        "GRANT SELECT ON {{ clickhouse_docker_llm_database }}.{{ clickhouse_docker_wall_recent_view }}\n"
        "               TO {{ clickhouse_docker_wall_reader_user }};"
    ) in task
    assert "      - clickhouse_docker_wall_reader_password\n" in (ROLE / "tasks/main.yml").read_text(encoding="utf-8")
