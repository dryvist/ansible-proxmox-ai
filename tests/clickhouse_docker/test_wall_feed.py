"""Check the wall feed: stopwords, refreshable views, HTTP handlers and wall_reader."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import jinja2
import yaml

from _wall_sql import (
    RECENT,
    ROLE,
    ROOT,
    WALL_MODES,
    WALL_TABLES,
    WORD_CLOUD,
    _defaults,
    _render,
    _statement,
    _statements,
)


WALL_SHAPES = {
    "claude": "claude",
    "codex": "codex",
    "antigravity": "antigravity",
    "gemini": "gemini",
    "zcode": "chat",
    "opencode": "chat",
}
YAML_11_SCALARS = ["n", "no", "null", "off", "on", "y", "yes"]
STOPWORDS_TASK = "Rebuild the wall stopwords table"
FINGERPRINT_TASK = "Fingerprint the wall view definitions"
COUNT_TASK = "Check which wall view definition is in place"
VALIDATE_TASK = "Check the wall view SELECT bodies against the live server"
DROP_TASK = "Drop the wall views when their definition changed"
CREATE_TASK = "Create the wall refreshable views"
GRANTS_TASK = "Provision the wall read-only ClickHouse user"


def _without_literals(sql: str) -> str:
    return re.sub(r"'(?:\\.|[^'\\])*'", "''", sql)


def _wall_tasks() -> list[dict]:
    return yaml.safe_load((ROLE / "tasks/wall_feed.yml").read_text(encoding="utf-8"))


def _task(name: str) -> dict:
    return next(task for task in _wall_tasks() if task.get("name") == name)


def _argv(name: str) -> list[str]:
    return _task(name)["ansible.builtin.command"]["argv"]


def _runs(task: dict, count_stdout: str) -> bool:
    """Whether the task's `when` lets it run, given the stdout of the views count check."""
    gate = jinja2.Environment().compile_expression(task["when"])
    return bool(gate(clickhouse_docker_wall_views_in_place={"stdout": count_stdout}))


def test_wall_sources_are_llm_tables_and_name_their_json_shape():
    defaults = _defaults()
    sources = defaults["clickhouse_docker_wall_sources"]

    assert [source["table"] for source in sources] == WALL_TABLES
    assert set(WALL_TABLES) <= set(defaults["clickhouse_docker_llm_tables"])
    assert {source["table"]: source["shape"] for source in sources} == WALL_SHAPES
    for source in sources:
        assert set(source) == {"table", "shape"}


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


def test_every_mode_renders_balanced_sql_with_no_template_left_over():
    for mode in WALL_MODES:
        for statement in _statements(_render("llm_wall.sql.j2", clickhouse_docker_wall_mode=mode)):
            bare = _without_literals(statement)
            assert "'" not in bare.replace("''", "")
            assert bare.count("(") == bare.count(")")
            assert "{{" not in statement and "{%" not in statement


def test_modes_render_the_statements_they_are_for_and_the_default_is_the_views():
    heads = {
        mode: [s.split("\n")[0] for s in _statements(_render("llm_wall.sql.j2", clickhouse_docker_wall_mode=mode))]
        for mode in WALL_MODES
    }

    assert heads == {
        "stopwords": ["CREATE OR REPLACE TABLE llm.stopwords"],
        "views": [
            "CREATE MATERIALIZED VIEW IF NOT EXISTS llm.word_cloud",
            "CREATE MATERIALIZED VIEW IF NOT EXISTS llm.recent_activity",
        ],
        "explain": ["EXPLAIN PLAN SELECT", "EXPLAIN PLAN SELECT"],
    }
    assert _statements(_render("llm_wall.sql.j2")) == _statements(
        _render("llm_wall.sql.j2", clickhouse_docker_wall_mode="views")
    )


def test_explain_mode_checks_exactly_the_select_bodies_the_views_are_created_from():
    checks = _statements(_render("llm_wall.sql.j2", clickhouse_docker_wall_mode="explain"))
    views = [
        s for s in _statements(_render("llm_wall.sql.j2", clickhouse_docker_wall_mode="views"))
        if s.startswith("CREATE MATERIALIZED VIEW")
    ]

    assert len(checks) == len(views) == 2
    for check, view in zip(checks, views):
        body = view.split("\nAS ", 1)[1].rsplit("\nCOMMENT ", 1)[0]
        assert check == "EXPLAIN PLAN " + body


def test_stopwords_table_is_rebuilt_from_every_default_word_through_the_tokeniser():
    statement = _statement("CREATE OR REPLACE TABLE llm.stopwords")
    defaults = _defaults()

    assert "(\n    word String\n)" in statement
    assert "splitByNonAlpha(lower(w))" in statement
    assert "'doesn\\'t'" in statement
    for word in defaults["clickhouse_docker_stopwords"] + defaults["clickhouse_docker_stopwords_extra"]:
        assert "'" + word.replace("\\", "\\\\").replace("'", "\\'") + "'" in statement


def test_word_cloud_refreshes_hourly_over_every_harness_and_keeps_top_words_per_source():
    statement = _statement(WORD_CLOUD)

    assert "REFRESH EVERY 1 HOUR\n(\n    source String,\n    word String,\n    n UInt64\n)" in statement
    assert statement.count("UNION ALL") == len(WALL_TABLES) - 1
    for table in WALL_TABLES:
        assert f"FROM llm.{table} FINAL" in statement
        assert f"if(msg_role = 'user', 'user', '{table}') AS source" in statement
    assert "ARRAY JOIN splitByNonAlpha(lower(body)) AS word" in statement
    assert "lengthUTF8(word) BETWEEN 3 AND 24" in statement
    # splitByNonAlpha keeps digits and non-ASCII bytes inside tokens; only ASCII letters pass.
    assert "AND match(word, '^[a-z]+$')" in statement
    assert "word NOT IN (SELECT word FROM llm.stopwords)" in statement
    assert statement.endswith("LIMIT 200 BY source\nCOMMENT ''")


def test_injected_prompts_are_dropped_per_part_from_user_rows_only():
    statement = _statement(WORD_CLOUD)

    assert statement.count("arrayFilter(part -> NOT (msg_role = 'user' AND match(part, '") == len(WALL_TABLES)
    assert "match(part, '^[[:space:]]*(<[a-z_-]+>|# AGENTS\\\\.md instructions|" in statement


def test_every_injected_prompt_pattern_is_in_the_rendered_clause():
    patterns = _defaults()["clickhouse_docker_wall_injected_prompt_patterns"]
    clause = re.search(r"match\(part, '(\^.*?)'\)", _statement(WORD_CLOUD)).group(1)

    assert "This session is being continued from a previous conversation" in patterns
    for pattern in patterns:
        re.compile(pattern)
        # The SQL literal escapes backslashes and quotes, as the template does.
        assert pattern.replace("\\", "\\\\").replace("'", "\\'") in clause


def test_recent_activity_refreshes_every_minute_and_never_returns_content():
    statement = _statement(RECENT)
    outer = statement.split("FROM\n(")[0]

    assert (
        "REFRESH EVERY 1 MINUTE\n(\n    ts DateTime,\n    source String,\n    model String,\n"
        "    role String,\n    chars UInt32\n)"
    ) in statement
    assert "content" not in outer
    assert "AS SELECT\n    ts,\n    source,\n    model,\n    message_role AS role,\n    chars\nFROM\n(" in statement
    assert "toUInt32(lengthUTF8(arrayStringConcat(msg_parts, ' '))) AS chars" in statement
    assert "lengthUTF8(content)" not in statement
    assert statement.count("UNION ALL") == len(WALL_TABLES) - 1
    for table in WALL_TABLES:
        assert f"FROM llm.{table} FINAL" in statement
    assert statement.endswith("ORDER BY ts DESC\nLIMIT 40\nCOMMENT ''")


def test_wall_views_carry_the_definition_fingerprint_as_their_comment():
    statements = _statements(_render("llm_wall.sql.j2", clickhouse_docker_wall_fingerprint="0a1b"))
    views = [s for s in statements if s.startswith("CREATE MATERIALIZED VIEW")]

    assert [s.split("\n")[-1] for s in views] == ["COMMENT '0a1b'", "COMMENT '0a1b'"]
    assert "COMMENT ''" in _render("llm_wall.sql.j2").split("CREATE MATERIALIZED VIEW")[1]


def test_wall_tasks_run_in_order_and_validate_the_selects_before_any_drop():
    names = [task.get("name") for task in _wall_tasks()]
    order = [STOPWORDS_TASK, FINGERPRINT_TASK, COUNT_TASK, VALIDATE_TASK, DROP_TASK, CREATE_TASK, GRANTS_TASK]
    positions = [names.index(name) for name in order]

    assert positions == sorted(positions)
    assert len(set(positions)) == len(order)


def test_each_wall_task_renders_its_mode_on_the_same_client_and_credential_path():
    modes = {STOPWORDS_TASK: "stopwords", FINGERPRINT_TASK: "views", VALIDATE_TASK: "explain", CREATE_TASK: "views"}
    for name, mode in modes.items():
        assert _task(name)["vars"]["clickhouse_docker_wall_mode"] == mode

    client = _argv(STOPWORDS_TASK)[:-1]
    for name in (VALIDATE_TASK, DROP_TASK, CREATE_TASK):
        assert _argv(name)[:-1] == client
    for name in (VALIDATE_TASK, DROP_TASK, CREATE_TASK, STOPWORDS_TASK):
        assert _task(name)["no_log"] is True


def test_drop_gate_drops_only_when_the_count_check_is_not_two():
    assert _task(VALIDATE_TASK)["when"] == _task(DROP_TASK)["when"]
    for name in (VALIDATE_TASK, DROP_TASK):
        task = _task(name)
        assert not _runs(task, "2")
        assert not _runs(task, "2\n")
        assert _runs(task, "1")
        assert _runs(task, "0")


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

    assert "bao_clickhouse_wall_secrets" in password
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


def _render_gateway_cidr(tofu_data: dict) -> str:
    template = _defaults()["clickhouse_docker_wall_gateway_cidr"]
    return jinja2.Template(template).render(hostvars={"localhost": {"tofu_data": tofu_data}}).strip()


def test_wall_gateway_cidr_reads_the_published_apps_subnet_and_is_empty_without_it():
    assert _render_gateway_cidr({"vlan_cidrs": {"apps": "192.0.2.0/24"}}) == "192.0.2.0/24"
    assert _render_gateway_cidr({"vlan_cidrs": {"other": "198.51.100.0/24"}}) == ""
    assert _render_gateway_cidr({}) == ""


def test_wall_reader_is_bound_to_the_gateway_subnet_and_the_converge_fails_closed():
    names = [task.get("name") for task in _wall_tasks()]
    assert_name = "Assert the wall gateway subnet is a bounded IPv4 CIDR"
    assert names.index(assert_name) == names.index(GRANTS_TASK) - 1

    that = _task(assert_name)["ansible.builtin.assert"]["that"]
    assert "clickhouse_docker_wall_gateway_cidr is ansible.utils.ipv4" in that
    assert "'/' in clickhouse_docker_wall_gateway_cidr" in that
    assert "(clickhouse_docker_wall_gateway_cidr.split('/')[1] | int) >= 16" in that
    assert "(clickhouse_docker_wall_gateway_cidr.split('/')[1] | int) <= 30" in that

    query = _task(GRANTS_TASK)["ansible.builtin.command"]["cmd"]
    assert query.count("HOST IP '{{ clickhouse_docker_wall_gateway_cidr }}'") == 2
    assert "HOST ANY" not in query
