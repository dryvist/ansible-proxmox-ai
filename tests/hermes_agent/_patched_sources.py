"""Derived PATCHED_* source constants for hermes_agent goal-mode tests.

Split out of conftest.py (2026-09, token budget): everything here is
produced by running the role's own patch tasks over the verbatim upstream
fragments in _pinned_sources.py / _pinned_goal_loop.py — never hand-copied,
so a hand-typed "expected" string can never drift from what the role
actually produces. conftest.py re-exports everything here (`from conftest
import PATCHED_*` keeps working) and owns the actual pytest fixtures,
which change for unrelated reasons.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment

from _pinned_sources import (
    PINNED_BOOST_CAP_SOURCE,
    PINNED_CRON_DELIVERY_SOURCE,
    PINNED_CRON_TIMEOUT_SOURCE,
)
from _pinned_sources_worker import (
    PINNED_HINDSIGHT_PREFETCH_SOURCE,
    PINNED_JUDGE_AVAILABLE_SOURCE,
    PINNED_JUDGE_CALL_SOURCE,
    PINNED_JUDGE_ERROR_SENTINEL_SOURCE,
    PINNED_SYNC_EXTERNAL_MEMORY_SOURCE,
)
from _pinned_goal_loop import PINNED_KANBAN_GOAL_LOOP_SOURCE
from _role_files import role_tasks


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


def _task(name: str) -> dict[str, Any]:
    tasks = role_tasks(ROLE_ROOT)
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


def _apply_rendered_runtime_patch(name: str, source: str) -> str:
    """Apply a replace task whose payload is rendered from its task vars."""
    task = _task(name)
    config = task["ansible.builtin.replace"]
    replacement = Environment(autoescape=False).from_string(config["replace"]).render(
        **task.get("vars", {})
    )
    patched, count = re.subn(
        config["regexp"], replacement, source, flags=re.MULTILINE
    )
    assert count == 1
    return patched


# Derived by running the role's own patch over the pinned upstream line,
# never hand-written — a hand-copied "expected" string can drift from what
# the role actually produces and would assert against itself.
# Upstream restructured this scan away; the clamp patch is retired and the
# assertion inverted, so the "patched" source is simply source without the
# unbounded reverse scan in it.
PATCHED_COMPRESSOR_SCAN_SOURCE = (
    "        for idx in range(start, end):\n"
)
PATCHED_CRON_DELIVERY_SOURCE = (
    # Code lives in the task's vars; `block:` is only an indent expression.
    _task("Rebind the built-in memory store for cron agents")["vars"][
        "_hermes_cron_memory_block"
    ]
    # The output-validity guard's own def line, needed for the postcondition
    # that checks it landed. Raw block text (unrendered Jinja placeholders
    # and all) — the postcondition only substring-matches the def line, which
    # carries no templating, so rendering is unnecessary here; the guard's
    # actual runtime behaviour is exec'd and rendered separately in
    # test_cron_output_validity.py.
    + _task("Patch Hermes cron delivery with an output-validity guard")[
        "ansible.builtin.blockinfile"
    ]["block"]
    # Upstream's own silence-matcher def line, pinned by
    # patches_verify.yml so a version bump that drops it fails loudly rather
    # than NameError-ing the guard above at runtime.
    + '\ndef _is_cron_silence_response(text: str) -> bool:\n'
    # The two call-site patches that used to route deliveries through
    # _cron_route() to the issues channel are retired (patches_cron_failure_
    # routing.yml) — upstream 2026.9.11 already calls _deliver_result/
    # _deliver_crash_failure with the right for_failure lane on its own, so
    # PINNED_CRON_DELIVERY_SOURCE needs no patch to reach that behaviour.
    + _apply_runtime_patch(
        "Route cron delivery content through the output-validity guard",
        _apply_runtime_patch(
            "Route cron delivery content through the markup guard",
            PINNED_CRON_DELIVERY_SOURCE,
        ),
    )
    # What survives from that retired pair: a script-fed job that exits 0
    # and declares its own failure. Minimal literal fragments — the real
    # call-site shape each regexp anchors on, not a hand-copied "expected"
    # patched string — feed the two surviving patches (
    # patches_cron_failure_routing.yml).
    + _apply_runtime_patch(
        "Route a self-declared cron failure through _cron_route",
        "    (\n"
        "        deliver_content, d.blocked_config, _silent_alert, d.incident_acked, "
        "d.failure_incident_id,\n"
        "    ) = _compose_run_delivery(\n"
        "        job, success=d.success, error=d.error, final_response=final_response,\n"
        "        output_file=output_file)\n",
    )
    + _apply_runtime_patch(
        "Deliver a self-declared cron failure through the failure lane",
        "    for_failure=not d.success,\n",
    )
)
# cron/scheduler.py carries the opt-in goal-mode runner too, and
# patches_verify.yml asserts on all of it against ONE source string — so this
# snippet has to represent every scheduler patch, not just the delivery pair.
# Built by running the real patch tasks, never by pasting their expected
# output: a hand-copied snippet is what let seven dead patches stay green.
PATCHED_CRON_DELIVERY_SOURCE += (
    _task("Patch Hermes cron scheduler with an opt-in goal-mode runner")[
        "ansible.builtin.blockinfile"
    ]["block"]
)
# Production applies the goal-mode submit replacement before the wall-clock
# patch rewrites the adjacent context line. Model that exact sequence on one
# source snippet: appending separately patched copies would leave the original
# direct submit in this synthetic module even though it is absent after a real
# converge.
PATCHED_CRON_TIMEOUT_SOURCE = _apply_runtime_patch(
    "Route the cron conversation through the goal-mode runner",
    PINNED_CRON_TIMEOUT_SOURCE,
)
for _cron_timeout_task_name in (
    "Resolve the aggregate cron wall clock beside the inactivity timeout",
    "Start the aggregate cron clock before submitting the conversation",
    "Initialize the independent cron timeout result flags",
    "Keep polling whenever either cron deadline is enabled",
    "Bound the final cron poll to the exact remaining wall budget",
    # "Guard the native inactivity comparison when that detector is
    # disabled" was retired by PR A (b3054ce1): its negative-lookahead
    # regexp on "Enforce the aggregate cron wall clock in the native poll
    # loop" already excludes the case that guard used to patch separately.
    "Enforce the aggregate cron wall clock in the native poll loop",
    "Raise the aggregate cron timeout before the inactivity handler",
):
    PATCHED_CRON_TIMEOUT_SOURCE = _apply_rendered_runtime_patch(
        _cron_timeout_task_name, PATCHED_CRON_TIMEOUT_SOURCE
    )
PATCHED_CRON_DELIVERY_SOURCE += (
    _task("Add aggregate cron wall-clock helpers")["ansible.builtin.blockinfile"][
        "block"
    ]
    + PATCHED_CRON_TIMEOUT_SOURCE
)
PATCHED_HINDSIGHT_PREFETCH_SOURCE = _apply_runtime_patch(
    "Patch Hermes auto-recall prefetch failure to log at warning, not debug",
    PINNED_HINDSIGHT_PREFETCH_SOURCE,
)
# Current upstream dropped the line entirely — also a passing state, since the
# assertion is now "the debug form is absent".
UPSTREAM_HINDSIGHT_PREFETCH_LINE_REMOVED = ""
PATCHED_RUN_AGENT_SOURCE = PINNED_SYNC_EXTERNAL_MEMORY_SOURCE
for _run_agent_task_name in (
    # Rewritten (2026-09): upstream combined the interrupted-turn guard and
    # the missing-input guard into ONE `if interrupted or not (...)` line,
    # merging what used to be two separately-anchored role patches.
    "Patch _sync_external_memory_for_turn to log its interrupted/missing-input skip",
    "Patch _sync_external_memory_for_turn to log its empty-flatten skip",
    "Patch _sync_external_memory_for_turn to log its swallowed exception",
):
    PATCHED_RUN_AGENT_SOURCE = _apply_runtime_patch(_run_agent_task_name, PATCHED_RUN_AGENT_SOURCE)
PATCHED_KANBAN_GOAL_LOOP_SOURCE = _apply_runtime_patch(
    "Patch Hermes kanban goal loop to retry judge errors without burning turns",
    _apply_runtime_patch(
        "Patch Hermes kanban goal loop to count consecutive judge failures",
        PINNED_KANBAN_GOAL_LOOP_SOURCE,
    ),
)
PATCHED_JUDGE_CALL_SOURCE = _apply_runtime_patch(
    "Patch Hermes goal judge to emit its model call latency",
    _apply_runtime_patch(
        "Patch Hermes goal judge to time its model call",
        PINNED_JUDGE_CALL_SOURCE,
    ),
)
PATCHED_JUDGE_AVAILABLE_SOURCE = _apply_runtime_patch(
    "Patch Hermes goal-judge availability probe to log why it declines",
    PINNED_JUDGE_AVAILABLE_SOURCE,
)
PATCHED_GOAL_JUDGE_SOURCE = (
    "DEFAULT_JUDGE_TIMEOUT = 60.0\n"
    + PINNED_JUDGE_ERROR_SENTINEL_SOURCE
    + PATCHED_KANBAN_GOAL_LOOP_SOURCE
    + PATCHED_JUDGE_CALL_SOURCE
)
