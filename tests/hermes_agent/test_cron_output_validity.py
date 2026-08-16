"""Self-check for the runtime cron output-validity guard, executed as shipped.

The block below (patches_cron_output_validity.yml) is patched into upstream's
scheduler at converge, wrapping _cron_markup_guard's call the same way that
guard wraps the raw delivery content. Rendering and running it here means
these assertions exercise the real function, not a restatement of it — see
test_alert_routing_helper.py for the sibling pattern this follows.

Two live incidents motivate the checks: an anomaly-sweep cron posted its
entire "report" as the single word "Interesting", and a separate job posted
"Now let me check for new tickets since last run..." — a mid-reasoning
fragment, not a finished turn. Both exited success=True and were delivered as
normal completed runs.
"""

from __future__ import annotations

import logging
import re
import types

from jinja2 import Environment

from conftest import ROLE_ROOT, _task


_ENV = Environment(autoescape=False)
_MARKER = "[ISSUES]"


def _load_guard(min_length=25, overrides=None, enabled=True):
    block = _task("Patch Hermes cron delivery with an output-validity guard")[
        "ansible.builtin.blockinfile"
    ]["block"]
    rendered = _ENV.from_string(block).render(
        hermes_agent_cron_output_validity_enabled=enabled,
        hermes_agent_cron_min_output_length=min_length,
        hermes_agent_cron_min_output_length_overrides=overrides or {},
    )
    mod = types.ModuleType("cron_output_validity")
    # _CRON_ISSUES_MARKER is defined by the markup-guard block this one is
    # patched alongside (patches_retry_and_markup.yml) — provided directly
    # here rather than re-rendering that whole block, since only the marker
    # name is a shared dependency.
    mod.__dict__.update(re=re, logger=logging.getLogger("test"), _CRON_ISSUES_MARKER=_MARKER)
    exec(compile(rendered, "cron-output-validity", "exec"), mod.__dict__)  # noqa: S102
    return mod


JOB = {"id": "anomaly-hunt", "name": "anomaly-hunt"}


def test_a_one_word_completion_is_rejected() -> None:
    mod = _load_guard()
    content = mod._cron_output_validity_guard(JOB, "/var/log/x.json", "Interesting", True)
    assert content.startswith(f"{_MARKER} :warning: Cron 'anomaly-hunt' failed:")
    assert "too short" in content


def test_a_mid_reasoning_fragment_is_rejected() -> None:
    mod = _load_guard()
    content = mod._cron_output_validity_guard(
        JOB, "/var/log/x.json",
        "Now let me check for new tickets since last run...", True,
    )
    assert content.startswith(f"{_MARKER} :warning: Cron 'anomaly-hunt' failed:")
    assert "interrupted" in content


def test_a_genuine_short_result_is_delivered_unchanged() -> None:
    mod = _load_guard()
    text = "No new tickets since last run."
    assert mod._cron_output_validity_guard(JOB, "/var/log/x.json", text, True) == text


def test_a_failed_run_is_never_rewritten() -> None:
    """A real failure summary must survive intact — rewriting it as a generic
    "too short" message would destroy the actual error for the operator."""
    mod = _load_guard()
    text = "Timeout"
    assert mod._cron_output_validity_guard(JOB, "/var/log/x.json", text, False) == text


def test_content_already_flagged_by_the_markup_guard_is_not_double_flagged() -> None:
    mod = _load_guard()
    text = ":warning: Cron job 'anomaly-hunt' produced unparsed tool-call markup instead of a report"
    assert mod._cron_output_validity_guard(JOB, "/var/log/x.json", text, True) == text


def test_per_job_override_can_disable_the_length_floor() -> None:
    mod = _load_guard(overrides={"anomaly-hunt": 0})
    text = "ok"
    assert mod._cron_output_validity_guard(JOB, "/var/log/x.json", text, True) == text


def test_the_truncation_heuristic_still_applies_with_the_floor_disabled() -> None:
    mod = _load_guard(overrides={"anomaly-hunt": 0})
    content = mod._cron_output_validity_guard(
        JOB, "/var/log/x.json", "Let me think about this...", True,
    )
    assert content.startswith(f"{_MARKER} :warning:")


def test_the_global_kill_switch_disables_the_guard_entirely() -> None:
    mod = _load_guard(enabled=False)
    text = "x"
    assert mod._cron_output_validity_guard(JOB, "/var/log/x.json", text, True) == text


def test_truncation_detector_edge_cases() -> None:
    mod = _load_guard()
    looks_truncated = mod._cron_output_looks_truncated
    # Ends mid-thought with no terminal punctuation.
    assert looks_truncated("Let me think about this")
    # Same lead-in, but a finished sentence — not truncated.
    assert not looks_truncated("I'll be back.")
    # Trailing ellipsis alone is enough, no lead-in needed.
    assert looks_truncated("This analysis is still in progress...")
    # A bare short word has neither signal; the length floor catches it
    # instead, which is the check meant for that shape.
    assert not looks_truncated("Interesting")
