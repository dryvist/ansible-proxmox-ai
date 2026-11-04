"""Check how each wall source's JSON shape becomes message rows."""

from _wall_sql import RECENT, WALL_TABLES, WORD_CLOUD, _branch, _message_rows, _statement


def test_claude_rows_take_role_and_model_from_the_message_json_and_text_from_text_blocks():
    branch = _branch(_statement(WORD_CLOUD), "claude")

    # Harness-injected context and the resumed-session summary are not messages.
    assert (
        "if(JSONExtractBool(raw, 'isMeta') OR JSONExtractBool(raw, 'isCompactSummary'), '', "
        "JSONExtractString(raw, 'type')) AS msg_role"
    ) in branch
    assert "JSONExtractString(raw, 'message', 'model') AS msg_model" in branch
    assert "JSONType(raw, 'message', 'content') = 'String'" in branch
    assert "[JSONExtractString(raw, 'message', 'content')]" in branch
    assert "JSONExtractArrayRaw(raw, 'message', 'content')" in branch
    assert "JSONExtractString(b, 'type') = 'text'" in branch
    assert "WHERE msg_role IN ('user', 'assistant')" in branch


def test_codex_rows_are_response_messages_with_payload_role_and_typed_text():
    branch = _branch(_statement(WORD_CLOUD), "codex")

    assert "JSONExtractString(raw, 'payload', 'type') = 'message'" in branch
    assert "JSONExtractString(raw, 'payload', 'role'), '') AS msg_role" in branch
    assert "JSONExtractString(raw, 'payload', 'role') = 'user', 'input_text', 'output_text'" in branch
    assert "JSONExtractArrayRaw(raw, 'payload', 'content')" in branch
    assert "input_image" not in branch
    assert "'' AS msg_model" in branch


def test_antigravity_rows_are_user_prompts_history_and_model_output_only():
    branch = _branch(_statement(WORD_CLOUD), "antigravity")

    assert "JSONExtractString(raw, 'type') = 'USER_INPUT'" in branch
    assert "JSONExtractString(raw, 'source') = 'USER_EXPLICIT'" in branch
    assert "(NOT JSONHas(raw, 'type'))" in branch
    assert "JSONType(raw, 'display') = 'String'" in branch
    assert "JSONExtractString(raw, 'source') = 'MODEL'" in branch
    assert "IN ('PLANNER_RESPONSE', 'GENERIC')" in branch
    assert "JSONType(raw, 'content') = 'String'" in branch
    assert "JSONExtractString(raw, 'display'))] AS msg_parts" in branch


def test_gemini_shape_reads_content_objects_and_skips_thought_parts():
    branch = _branch(_statement(WORD_CLOUD), "gemini")

    assert "JSONExtractString(raw, 'role') = 'model', 'assistant', ''" in branch
    assert "JSONExtractArrayRaw(raw, 'parts')" in branch
    assert "(NOT JSONExtractBool(b, 'thought'))" in branch


def test_gemini_rows_report_as_the_antigravity_source_and_other_tables_keep_their_name():
    # Gemini CLI is now AGY (Antigravity): one cloud, not two.
    for statement in (_statement(WORD_CLOUD), _statement(RECENT)):
        assert "'antigravity'" in _branch(statement, "gemini")
        assert "'gemini'" not in _branch(statement, "gemini")
        for table in ("claude", "codex", "antigravity", "zcode", "opencode"):
            assert f"'{table}'" in _branch(statement, table)


def test_chat_shape_reads_top_level_role_content_and_model():
    statement = _statement(WORD_CLOUD)

    for table in ("zcode", "opencode"):
        branch = _branch(statement, table)
        assert "JSONExtractString(raw, 'role') AS msg_role" in branch
        assert "JSONExtractString(raw, 'model') AS msg_model" in branch
        assert "JSONType(raw, 'content') = 'String'" in branch
        assert "JSONExtractArrayRaw(raw, 'content')" in branch
        assert "JSONExtractString(b, 'type') = 'text'" in branch


def test_both_views_read_each_table_through_the_same_message_rows():
    word_cloud = _statement(WORD_CLOUD)
    recent = _statement(RECENT)

    for table in WALL_TABLES:
        assert _message_rows(_branch(word_cloud, table)) == _message_rows(_branch(recent, table))
