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
# Verbatim upstream lines at the pinned tag (v2026.7.7.2), indentation
# included — `_apply_runtime_patch` re-runs the role's own regexp against
# these, so a copy that drifts from upstream would silently stop patching
# and the test would go green on nothing.
PINNED_TC_BOOST_CAP_SOURCE = (
    "                                _tc_boost_cap = max("
    "32768, _tc_requested_cap or 0)\n"
)
PINNED_BOOST_CAP_SOURCE = (
    "            _boost_cap = max(32768, _requested_cap or 0)\n"
)
PINNED_COMPRESSOR_SCAN_SOURCE = (
    "        for idx in range(end - 1, start - 1, -1):\n"
)
PINNED_PROTOCOL_VIOLATION_SOURCE = (
    '                    "worker exited cleanly (rc=0) without calling "\n'
    '                    "kanban_complete or kanban_block — protocol violation"\n'
)
PINNED_CRON_DELIVERY_SOURCE = (
    "            deliver_content = final_response if success else "
    "_summarize_cron_failure_for_delivery(job, error)\n"
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


# Derived by running the role's own patch over the pinned upstream line,
# never hand-written — a hand-copied "expected" string can drift from what
# the role actually produces and would assert against itself.
PATCHED_COMPRESSOR_SCAN_SOURCE = _apply_runtime_patch(
    "Patch hermes-agent context summary scan to use the live message bound",
    PINNED_COMPRESSOR_SCAN_SOURCE,
)
PATCHED_CRON_DELIVERY_SOURCE = _apply_runtime_patch(
    "Route cron delivery content through the markup guard",
    PINNED_CRON_DELIVERY_SOURCE,
)


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
    compressor_source: str = PATCHED_COMPRESSOR_SCAN_SOURCE,
    cron_scheduler_source: str = PATCHED_CRON_DELIVERY_SOURCE,
) -> tuple[bool, ...]:
    task = _task("Assert installed Hermes pinned-source patches")
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
        "hermes_agent_compressor_source": compressor_source,
        "hermes_agent_cron_scheduler_source": cron_scheduler_source,
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
    hindsight_group_vars = yaml.safe_load(
        (REPO_ROOT / "inventory/group_vars/hindsight_group.yml").read_text()
    )
    hindsight_compose = (
        REPO_ROOT / "roles/hindsight_docker/templates/docker-compose.yml.j2"
    ).read_text()
    router_defaults = yaml.safe_load(
        (REPO_ROOT / "roles/llm_router/defaults/main.yml").read_text()
    )
    router_config = (REPO_ROOT / "roles/llm_router/templates/config.yaml.j2").read_text()
    config = (ROLE_ROOT / "templates" / "config.yaml.j2").read_text()

    hermes_alias = "hermes-default"
    # The alias map no longer names physical ids: they come from two selector
    # vars that are the single record of what the serving host actually serves.
    # Pinning literals here is what let all four aliases drift to unroutable
    # models at once (2026-07-28, every one a live 404), so follow the
    # indirection instead of re-pinning the ids under a new name.
    hermes_backend = router_defaults["llm_router_primary_model"]
    judge_backend = router_defaults["llm_router_small_model"]
    assert group_vars["hermes_brain_model"] == hermes_alias
    # The judge rides its own alias now — a judge on the worker's model is
    # self-preference bias, and the two serialize against one serving slot.
    assert group_vars["hermes_goal_judge_model"] == "goal-judge"
    assert judge_backend != hermes_backend
    assert defaults["hermes_agent_model"] == "{{ hermes_brain_model }}"
    assert defaults["hermes_agent_compression_model"] == "{{ hermes_brain_model }}"
    assert defaults["hermes_agent_memory_llm_model"] == "{{ hermes_brain_model }}"
    assert hindsight_group_vars["hindsight_docker_llm_model"] == "{{ hermes_brain_model }}"
    assert 'HINDSIGHT_API_LLM_MODEL: "{{ hindsight_docker_llm_model }}"' in hindsight_compose
    assert defaults["hermes_agent_model_max_tokens"] == 8192
    assert defaults["hermes_agent_context_compression_threshold"] == 0.75
    assert defaults["hermes_agent_brain_sync_enabled"] is False
    assert router_defaults["llm_router_model_group_aliases"] == {
        hermes_alias: "{{ llm_router_primary_model }}",
        "tool-calling": "{{ llm_router_primary_model }}",
        "goal-judge": "{{ llm_router_small_model }}",
        "interim-brain": "{{ llm_router_primary_model }}",
    }
    # Both selectors must be declared servable, or the alias indirection just
    # moves the 404 one level down.
    assert router_defaults["llm_router_servable_models"] == [
        "{{ llm_router_primary_model }}",
        "{{ llm_router_small_model }}",
    ]
    hermes_entries = [
        entry
        for entry in router_defaults["llm_router_large_models"]
        if entry["backend"] == hermes_backend
    ]
    assert hermes_entries == [{"backend": hermes_backend, "context_window": 65536}]
    assert router_defaults["llm_router_num_retries"] == 0
    # 429 = "the slot is busy", never "the work is impossible", so the router
    # absorbs it rather than failing the caller (#175). Not 0 — that setting
    # killed a cron mid-generation on 2026-07-24.
    assert router_defaults["llm_router_rate_limit_retries"] == 8
    assert "model_group_alias:" in router_config
    assert "llm_router_model_group_aliases.items()" in router_config
    # Pinned to the WORKER model structurally, not to a judge var that merely
    # happens to match — single-model serving policy, so config drift cannot pull
    # judge and worker apart (#162, superseding the separate-judge-var of #143).
    # The var that actually reaches auxiliary.goal_judge.model. The similarly
    # named `hermes_goal_judge_model` in group_vars drives no inference at all —
    # it names the router alias the compress-death assert checks. Repointing
    # THAT one does not unpin the judge; that mistake was made on 2026-07-29,
    # and this assertion pair is what makes the difference legible.
    assert defaults["hermes_agent_kanban_goal_judge_model"] == "{{ hermes_agent_model }}"
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
    read_task = _task("Read installed Hermes pinned-source patches")
    assert "ansible.builtin.slurp" in read_task
    assert read_task["register"] == "hermes_agent_goal_mode_sources"
    # The assert task indexes results[] positionally, so the slurp order is
    # load-bearing: a file inserted anywhere but the end silently re-points
    # every later assertion at the wrong source.
    assert [path.split("}}/")[-1] for path in read_task["loop"]] == [
        "tools/kanban_tools.py",
        "hermes_cli/kanban_db.py",
        "hermes_cli/goals.py",
        "agent/conversation_loop.py",
        "agent/auxiliary_client.py",
        "agent/context_compressor.py",
        "cron/scheduler.py",
    ]

    assert_task = _task("Assert installed Hermes pinned-source patches")
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
    assert "for idx in range(min(end, len(messages)) - 1, start - 1, -1):" in conditions
    assert "deliver_content = _cron_markup_guard(job, output_file," in conditions
    assert (
        "registered for dispatcher-spawned workers (HERMES_KANBAN_TASK "
        in conditions
    )
    assert any(
        "_boost_cap = agent.max_tokens if agent.max_tokens else max(" in condition
        and ".count(" in condition
        and ") == 2" in condition
        for condition in assert_task["ansible.builtin.assert"]["that"]
    )
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
        + _apply_runtime_patch(
            "Patch Hermes protocol-violation message to name the "
            "model-did-not-call case",
            PINNED_PROTOCOL_VIOLATION_SOURCE,
        )
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
    retry_source += _apply_runtime_patch(
        "Patch hermes-agent retry boost to respect the configured max_tokens ceiling",
        PINNED_TC_BOOST_CAP_SOURCE,
    )
    retry_source += _apply_runtime_patch(
        "Patch hermes-agent length-continuation boost to respect the configured "
        "max_tokens ceiling",
        PINNED_BOOST_CAP_SOURCE,
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
    # Each newly-covered patch, dropped one at a time. Without these the
    # assertions could be decorative — present in the task file, but
    # incapable of going red on the failure they exist to catch.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            compressor_source=PINNED_COMPRESSOR_SCAN_SOURCE,
        )
    )
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            cron_scheduler_source=PINNED_CRON_DELIVERY_SOURCE,
        )
    )
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source.replace(
                "registered for dispatcher-spawned workers (HERMES_KANBAN_TASK ",
                "",
            ),
            retry_source,
            auxiliary_source,
        )
    )
    # The length-continuation boost dropped while the _tc_ one landed. The
    # needle `_boost_cap = agent.max_tokens ...` still occurs once (inside
    # the _tc_boost_cap line), so only the ==2 count catches this — a plain
    # substring assertion would pass here with the patch missing.
    retry_source_tc_only = retry_source.replace(
        "_boost_cap = agent.max_tokens if agent.max_tokens else max("
        "32768, _requested_cap or 0)",
        "_boost_cap = max(32768, _requested_cap or 0)",
    )
    assert "_tc_boost_cap = agent.max_tokens" in retry_source_tc_only
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source_tc_only,
            auxiliary_source,
        )
    )
