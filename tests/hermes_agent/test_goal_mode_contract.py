from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml
from jinja2 import Environment


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
ACTIVE_STATUSES = (
    "triage",
    "todo",
    "scheduled",
    "ready",
    "blocked",
    "review",
)
PINNED_CREATE_TASK_SOURCE = '''\
def create_task(conn, *, idempotency_key=None, goal_mode=False, goal_max_turns=None):
    if idempotency_key:
        row = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key = ? "
            "AND status != 'archived' "
            "ORDER BY created_at DESC LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        if row:
            return row["id"]
    raise RuntimeError("insert path")
'''
PINNED_GOAL_COMPLETION_SOURCE = "                    verdict, reason, _ = judge_goal(\n"
PINNED_RETRY_DELAY_SOURCE = (
    "                wait_time = _retry_after if _retry_after else "
    "jittered_backoff(retry_count, base_delay=2.0, max_delay=60.0)\n"
)
PINNED_INVALID_RESPONSE_RETRY_SOURCE = (
    "                    wait_time = jittered_backoff("
    "retry_count, base_delay=5.0, max_delay=120.0)\n"
)
PINNED_ADAPTIVE_BACKOFF_SOURCE = (
    "                if is_rate_limited and not _retry_after:\n"
)
PINNED_TRANSPORT_RECOVERY_SOURCE = (
    "                    if not _retry.primary_recovery_attempted and "
    "agent._try_recover_primary_transport(\n"
)
PINNED_TOKEN_USAGE_SOURCE = (
    "                    if agent.verbose_logging:\n"
    '                        logging.debug(f"Token usage: {usage}")\n'
)
MALFORMED_TOKEN_USAGE_SOURCE = (
    "                   if True:\n"
    '                       logging.debug(f"Token usage: {usage}")\n'
)
PATCHED_TOKEN_USAGE_SOURCE = (
    "                    if True:\n"
    '                        logging.debug(f"Token usage: {usage}")\n'
)
PINNED_WORKER_SPAWN_SOURCE = '''\
def build_worker_argv(task, prompt):
    cmd = []
    cmd.extend([
        "chat",
        "-q", prompt,
    ])
    return cmd
'''


def _task(name: str) -> dict[str, Any]:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "main.yml").read_text())
    return next(item for item in tasks if item.get("name") == name)


def _replace_task(name: str) -> dict[str, str]:
    return _task(name)["ansible.builtin.replace"]


def _apply_runtime_patch(name: str, source: str) -> str:
    config = _replace_task(name)
    patched, count = re.subn(
        config["regexp"],
        config["replace"],
        source,
        flags=re.MULTILINE,
    )
    assert count == 1
    return patched


@contextmanager
def _write_txn(conn: sqlite3.Connection) -> Iterator[None]:
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def _patched_create_task() -> Any:
    source = _apply_runtime_patch(
        "Patch Hermes idempotent create to reconcile goal-mode fields",
        PINNED_CREATE_TASK_SOURCE,
    )
    namespace: dict[str, Any] = {"write_txn": _write_txn}
    exec(source, namespace)
    return namespace["create_task"]


def _task_db(
    *, status: str, goal_mode: int = 0, goal_max_turns: int | None = None
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE tasks ("
        "id TEXT PRIMARY KEY, idempotency_key TEXT, status TEXT, created_at INTEGER, "
        "goal_mode INTEGER, goal_max_turns INTEGER)"
    )
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
        ("existing", "same-slot", status, 1, goal_mode, goal_max_turns),
    )
    conn.commit()
    return conn


def _goal_fields(conn: sqlite3.Connection) -> tuple[int, int | None]:
    row = conn.execute(
        "SELECT goal_mode, goal_max_turns FROM tasks WHERE id = 'existing'"
    ).fetchone()
    return row["goal_mode"], row["goal_max_turns"]


def _render_reviewer_prompt(goal_mode: bool) -> str:
    defaults = yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text())
    environment = Environment(autoescape=False)
    environment.filters["bool"] = bool
    return environment.from_string(defaults["hermes_agent_reviewer_card_prompt"]).render(
        hermes_agent_kanban_goal_mode=goal_mode,
        hermes_agent_kanban_goal_max_turns=3,
        hermes_agent_slack_hermes_all_channel="C00000000",
    )


def _source_postconditions(
    completion_source: str,
    reconcile_source: str,
    retry_source: str,
    auxiliary_source: str,
) -> tuple[bool, ...]:
    task = _task("Assert installed Hermes goal-mode source patches")
    environment = Environment(autoescape=False)
    context = {
        "hermes_agent_goal_completion_source": completion_source,
        "hermes_agent_goal_reconcile_source": reconcile_source,
        "hermes_agent_goal_judge_source": (
            "DEFAULT_JUDGE_TIMEOUT = 60.0\n"
        ),
        "hermes_agent_kanban_goal_judge_timeout_seconds": 60,
        "hermes_agent_retry_source": retry_source,
        "hermes_agent_auxiliary_source": auxiliary_source,
    }
    return tuple(
        bool(environment.compile_expression(condition)(**context))
        for condition in task["ansible.builtin.assert"]["that"]
    )


def test_goal_completion_patch_uses_current_judge_contract() -> None:
    patched = _apply_runtime_patch(
        "Patch Hermes goal completion gate for the four-value judge result",
        PINNED_GOAL_COMPLETION_SOURCE,
    )
    assert "verdict, reason, _, _ = judge_goal(" in patched


@pytest.mark.parametrize(
    "source",
    (
        PINNED_TOKEN_USAGE_SOURCE,
        MALFORMED_TOKEN_USAGE_SOURCE,
        PATCHED_TOKEN_USAGE_SOURCE,
    ),
)
def test_token_usage_metric_patch_normalizes_known_source_states(source: str) -> None:
    patched = _apply_runtime_patch(
        "Enable prompt-safe Hermes token usage metrics at DEBUG",
        source,
    )
    assert patched == PATCHED_TOKEN_USAGE_SOURCE


def test_model_calls_retry_once_after_fifteen_seconds() -> None:
    config_template = (ROLE_ROOT / "templates" / "config.yaml.j2").read_text()
    assert "Upstream counts total attempts" in config_template
    assert "api_max_retries: 2" in config_template
    assert "transient_retries: 1" in config_template

    patched = _apply_runtime_patch(
        "Patch Hermes exception retry delay for the local serial backend",
        PINNED_RETRY_DELAY_SOURCE,
    )
    assert "wait_time = 15.0" in patched
    assert "jittered_backoff" not in patched

    invalid_response = _apply_runtime_patch(
        "Patch Hermes invalid-response retry delay for the local serial backend",
        PINNED_INVALID_RESPONSE_RETRY_SOURCE,
    )
    assert "wait_time = 15.0" in invalid_response

    adaptive = _apply_runtime_patch(
        "Disable adaptive rate-limit backoff for the local serial backend",
        PINNED_ADAPTIVE_BACKOFF_SOURCE,
    )
    assert "if False and" in adaptive

    transport = _apply_runtime_patch(
        "Disable the extra transport-recovery attempt cycle",
        PINNED_TRANSPORT_RECOVERY_SOURCE,
    )
    assert "if False and" in transport

    tasks = (ROLE_ROOT / "tasks" / "main.yml").read_text()
    assert "_TRANSIENT_RETRY_BACKOFF_BASE = 15.0" in tasks
    assert "status in (408, 429)" in tasks
    assert 'resolved_provider != "custom"' in tasks


def test_worker_spawn_patch_enters_quiet_goal_loop_path() -> None:
    patched = _apply_runtime_patch(
        "Patch Hermes Kanban workers to enter the quiet goal-loop path",
        PINNED_WORKER_SPAWN_SOURCE,
    )
    quiet_expansion = '        *(["--quiet"] if task.goal_mode else []),\n'
    assert patched.count(quiet_expansion) == 1
    assert patched.index('"chat"') < patched.index('["--quiet"]')
    assert patched.index('["--quiet"]') < patched.index('"-q", prompt')

    patched_again = _apply_runtime_patch(
        "Patch Hermes Kanban workers to enter the quiet goal-loop path",
        patched,
    )
    assert patched_again == patched

    duplicated = PINNED_WORKER_SPAWN_SOURCE.replace(
        '        "-q", prompt,\n',
        quiet_expansion * 17 + '        "-q", prompt,\n',
    )
    normalized = _apply_runtime_patch(
        "Patch Hermes Kanban workers to enter the quiet goal-loop path",
        duplicated,
    )
    assert normalized == patched

    namespace: dict[str, Any] = {}
    exec(patched, namespace)
    task_type = type("Task", (), {})
    goal_task = task_type()
    goal_task.goal_mode = True
    ordinary_task = task_type()
    ordinary_task.goal_mode = False
    assert namespace["build_worker_argv"](goal_task, "work") == [
        "chat",
        "--quiet",
        "-q",
        "work",
    ]
    assert namespace["build_worker_argv"](ordinary_task, "work") == [
        "chat",
        "-q",
        "work",
    ]


@pytest.mark.parametrize("status", ACTIVE_STATUSES)
def test_idempotent_create_upgrades_active_same_slot(status: str) -> None:
    conn = _task_db(status=status)

    task_id = _patched_create_task()(
        conn,
        idempotency_key="same-slot",
        goal_mode=True,
        goal_max_turns=3,
    )

    assert task_id == "existing"
    assert _goal_fields(conn) == (1, 3)


def test_idempotent_create_preserves_existing_max_when_new_max_is_null() -> None:
    conn = _task_db(status="blocked", goal_max_turns=5)

    _patched_create_task()(
        conn,
        idempotency_key="same-slot",
        goal_mode=True,
        goal_max_turns=None,
    )

    assert _goal_fields(conn) == (1, 5)


def test_idempotent_create_does_not_reconcile_running_task() -> None:
    conn = _task_db(status="running", goal_max_turns=5)

    assert (
        _patched_create_task()(
            conn,
            idempotency_key="same-slot",
            goal_mode=True,
            goal_max_turns=3,
        )
        == "existing"
    )
    assert _goal_fields(conn) == (0, 5)


@pytest.mark.parametrize("status", ("done", "archived"))
def test_idempotent_create_does_not_mutate_terminal_history(status: str) -> None:
    conn = _task_db(status=status, goal_max_turns=5)

    if status == "archived":
        with pytest.raises(RuntimeError, match="insert path"):
            _patched_create_task()(
                conn,
                idempotency_key="same-slot",
                goal_mode=True,
                goal_max_turns=3,
            )
    else:
        assert (
            _patched_create_task()(
                conn,
                idempotency_key="same-slot",
                goal_mode=True,
                goal_max_turns=3,
            )
            == "existing"
        )

    assert _goal_fields(conn) == (0, 5)


def test_enqueuer_goal_flags_follow_the_role_toggle() -> None:
    enqueuer = (ROLE_ROOT / "templates" / "kanban-enqueue-recurring.sh.j2").read_text()
    assert (
        "{% if hermes_agent_kanban_goal_mode | bool %} --goal --goal-max-turns "
        "{{ hermes_agent_kanban_goal_max_turns }}{% endif %}"
        in enqueuer
    )
    assert "hermes send --to slack:{{ hermes_agent_slack_hermes_all_channel }}" in enqueuer
    assert "kind=needs_input" in enqueuer
    assert "status=pending" not in enqueuer


def test_reviewer_child_goal_fields_follow_the_role_toggle() -> None:
    enabled = _render_reviewer_prompt(True)
    disabled = _render_reviewer_prompt(False)

    assert "initial_status=blocked, goal_mode=true, and goal_max_turns=3" in enabled
    assert "preserves this card's bounded goal loop" in enabled
    assert "goal_mode=true" not in disabled
    assert "goal_max_turns=" not in disabled
    assert "bounded goal loop" not in disabled


def test_hermes_inference_paths_use_the_declared_alias() -> None:
    defaults = yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text())
    group_vars = yaml.safe_load((REPO_ROOT / "inventory/group_vars/all.yml").read_text())
    router_defaults = yaml.safe_load(
        (REPO_ROOT / "roles/llm_router/defaults/main.yml").read_text()
    )
    router_config = (REPO_ROOT / "roles/llm_router/templates/config.yaml.j2").read_text()
    config = (ROLE_ROOT / "templates" / "config.yaml.j2").read_text()

    hermes_alias = "hermes-default"
    hermes_backend = "mlx-community/Qwen3.5-9B-OptiQ-4bit"
    assert group_vars["hermes_brain_model"] == hermes_alias
    assert group_vars["hermes_goal_judge_model"] == hermes_alias
    assert defaults["hermes_agent_model"] == "{{ hermes_brain_model }}"
    assert defaults["hermes_agent_compression_model"] == "{{ hermes_brain_model }}"
    assert defaults["hermes_agent_memory_llm_model"] == "{{ hermes_brain_model }}"
    assert defaults["hermes_agent_model_max_tokens"] == 8192
    assert defaults["hermes_agent_context_compression_threshold"] == 0.75
    assert defaults["hermes_agent_brain_sync_enabled"] is False
    assert router_defaults["llm_router_model_group_aliases"] == {
        hermes_alias: hermes_backend,
        "tool-calling": "mlx-community/Qwen3-Next-80B-A3B-Instruct-4bit",
        "goal-judge": "mlx-community/Qwen3.6-27B-mxfp4",
    }
    hermes_entries = [
        entry
        for entry in router_defaults["llm_router_large_models"]
        if entry["backend"] == hermes_backend
    ]
    assert hermes_entries == [{"backend": hermes_backend, "context_window": 65536}]
    assert router_defaults["llm_router_num_retries"] == 0
    assert router_defaults["llm_router_rate_limit_retries"] == 0
    assert "model_group_alias:" in router_config
    assert "llm_router_model_group_aliases.items()" in router_config
    assert defaults["hermes_agent_kanban_goal_judge_model"] == "{{ hermes_goal_judge_model }}"
    assert defaults["hermes_agent_kanban_goal_judge_timeout_seconds"] == 60
    assert "goal_judge:" in config
    assert "model: {{ hermes_agent_kanban_goal_judge_model | to_json }}" in config
    assert "base_url: '{{ hermes_agent_model_base_url }}'" in config


def test_group_vars_reads_canonical_zammad_mcp_pair() -> None:
    group_vars = (REPO_ROOT / "inventory/group_vars/hermes_agent_group.yml").read_text()
    assert "bao_local_llm_secrets.ZAMMAD_MCP_URL" in group_vars
    assert "bao_local_llm_secrets.ZAMMAD_MCP_TOKEN" in group_vars
    assert "bao_local_llm_secrets.ZAMMAD_API_TOKEN" not in group_vars
    assert "ZAMMAD_MCP_URL | regex_replace('/api/v1/?$', '')" in group_vars
    assert "else lookup('env', 'ZAMMAD_URL')" in group_vars


def test_prompt_catalog_build_keeps_a_gc_root() -> None:
    build_task = _task("Build the pinned prompt catalog on the controller")
    command = build_task["ansible.builtin.command"]["cmd"]
    assert "--out-link /tmp/hermes-agent-prompts" in command
    assert "--no-link" not in command


def test_installed_source_postconditions_fail_closed() -> None:
    read_task = _task("Read installed Hermes goal-mode source patches")
    assert "ansible.builtin.slurp" in read_task
    assert read_task["register"] == "hermes_agent_goal_mode_sources"

    assert_task = _task("Assert installed Hermes goal-mode source patches")
    conditions = " ".join(assert_task["ansible.builtin.assert"]["that"])
    assert "verdict, reason, _, _ = judge_goal(" in conditions
    assert "DEFAULT_JUDGE_TIMEOUT =" in conditions
    assert "SELECT id, status FROM tasks" in conditions
    assert (
        'if goal_mode and row["status"] in ("triage", "todo", "scheduled", '
        '"ready", "blocked", "review"):'
        in conditions
    )
    assert "goal_max_turns = COALESCE(?, goal_max_turns)" in conditions
    assert any(
        '*(["--quiet"] if task.goal_mode else []),' in condition
        and ".count(" in condition
        and ") == 1" in condition
        for condition in assert_task["ansible.builtin.assert"]["that"]
    )
    assert any(
        '"-q", prompt,' in condition
        and ".count(" in condition
        and ") == 1" in condition
        for condition in assert_task["ansible.builtin.assert"]["that"]
    )
    assert '["--quiet"]' in conditions
    assert "WHERE id = ? AND status IN" in conditions
    assert "_TRANSIENT_RETRY_BACKOFF_BASE = 15.0" in conditions
    assert "status in (408, 429)" in conditions
    assert 'resolved_provider != "custom"' in conditions
    assert "Token usage:" in conditions
    assert "update the pinned-source patches" in assert_task["ansible.builtin.assert"][
        "fail_msg"
    ]

    completion_source = _apply_runtime_patch(
        "Patch Hermes goal completion gate for the four-value judge result",
        PINNED_GOAL_COMPLETION_SOURCE,
    )
    reconcile_source = _apply_runtime_patch(
        "Patch Hermes idempotent create to reconcile goal-mode fields",
        PINNED_CREATE_TASK_SOURCE,
    )
    reconcile_source = (
        _apply_runtime_patch(
            "Patch Hermes Kanban workers to enter the quiet goal-loop path",
            PINNED_WORKER_SPAWN_SOURCE,
        )
        + reconcile_source
    )
    retry_source = "".join(
        (
            _apply_runtime_patch(
                "Patch Hermes exception retry delay for the local serial backend",
                PINNED_RETRY_DELAY_SOURCE,
            ),
            _apply_runtime_patch(
                "Patch Hermes invalid-response retry delay for the local serial backend",
                PINNED_INVALID_RESPONSE_RETRY_SOURCE,
            ),
            _apply_runtime_patch(
                "Disable adaptive rate-limit backoff for the local serial backend",
                PINNED_ADAPTIVE_BACKOFF_SOURCE,
            ),
            _apply_runtime_patch(
                "Disable the extra transport-recovery attempt cycle",
                PINNED_TRANSPORT_RECOVERY_SOURCE,
            ),
        )
    )
    retry_source += "# Log request details if verbose\n        if True:\n"
    retry_source += _apply_runtime_patch(
        "Enable prompt-safe Hermes token usage metrics at DEBUG",
        PINNED_TOKEN_USAGE_SOURCE,
    )
    auxiliary_source = "\n".join(
        (
            "_TRANSIENT_RETRY_BACKOFF_BASE = 15.0",
            "return isinstance(status, int) and (status in (408, 429) or 500 <= status < 600)",
            'if should_fallback and (is_auto or (is_capacity_error and resolved_provider != "custom")):',
        )
    )
    assert all(
        _source_postconditions(
            completion_source, reconcile_source, retry_source, auxiliary_source
        )
    )
    assert not all(
        _source_postconditions(
            PINNED_GOAL_COMPLETION_SOURCE,
            PINNED_CREATE_TASK_SOURCE,
            PINNED_RETRY_DELAY_SOURCE,
            "",
        )
    )
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source.replace("COALESCE(?, goal_max_turns)", "?"),
            retry_source,
            auxiliary_source,
        )
    )
