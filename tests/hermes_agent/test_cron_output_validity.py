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


def _is_cron_silence_response(text):
    """Stand-in for upstream's real matcher (gateway.response_filters,
    shared with the webhook lane). Only needs to prove THIS guard defers to
    whatever the real one says, not reproduce its bracketed/bracketless
    variant handling — that contract belongs to and is tested by upstream.
    """
    return text.strip().upper() in ("[SILENT]", "SILENT", "NO_REPLY", "NO REPLY")


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
    # _CRON_ISSUES_MARKER and _is_cron_silence_response are both defined
    # elsewhere in upstream's cron/scheduler.py, in scope by the time this
    # guard actually runs — provided directly here rather than re-rendering
    # the whole module, since only the names are the shared dependency.
    mod.__dict__.update(
        re=re,
        logger=logging.getLogger("test"),
        _CRON_ISSUES_MARKER=_MARKER,
        _is_cron_silence_response=_is_cron_silence_response,
    )
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


def test_a_complete_report_closing_with_the_mandated_attribution_is_delivered() -> None:
    """The prompt catalog REQUIRES every report to close with an attribution
    line ("— model: <id>"), which ends in a bare identifier carrying no
    terminal punctuation. A report that ALSO opens with a narration lead-in
    therefore satisfied both halves of the truncation heuristic and was
    failed — while being a finished report that complied with the prompt.

    Observed twice in production on splunk-triage, both on complete reports;
    one of them carried the hardware-error finding that opened a separate
    ticket. The failure is not rare: the lead-in half is entirely at the
    model's discretion, so any job whose model narrates before reporting is
    exposed on every run.

    Both texts below are the real openings and closings from those runs.
    """
    mod = _load_guard()
    for text in (
        "Now let me compile the full report:\n\n"
        "**Conclusion:** All systems stable. Key improvements: openbao_audit "
        "normalizing, sendmodalert halved, homepage crash loop down 63%. "
        "No new anomalies.\n\n"
        "— model: hermes-default",
        "Now let me update the memory with this run's findings and compare "
        "against the previous baseline.\n\n"
        "Clean: Splunk internal ERRs 0, auth failures 4, all indexes fresh.\n\n"
        "— model: hermes-default",
    ):
        assert mod._cron_output_validity_guard(JOB, "/var/log/x.json", text, True) == text


def test_a_rejection_quotes_the_text_it_judged() -> None:
    """Vikunja 1935: a verdict of "interrupted" must carry the evidence so it
    can be audited from the channel without opening the artifact."""
    mod = _load_guard()
    content = mod._cron_output_validity_guard(
        JOB, "/var/log/x.json", "Now let me check for new tickets since last run...", True)
    assert "trails off with an ellipsis" in content
    assert "tickets since last run..." in content
    content = mod._cron_output_validity_guard(
        JOB, "/var/log/x.json", "Let me look at the remaining hosts and then", True)
    assert "narration lead-in 'Let me look" in content
    assert "never reaches a terminal mark" in content


def test_a_fragment_is_still_rejected_when_it_carries_an_attribution() -> None:
    """Looking past the footer must not blind the ellipsis signal: a run that
    trailed off and still emitted the footer stays rejected."""
    mod = _load_guard()
    text = "Now let me check the remaining hosts...\n\n— model: hermes-default"
    content = mod._cron_output_validity_guard(JOB, "/var/log/x.json", text, True)
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


def test_per_job_override_of_zero_exempts_the_job_from_both_checks() -> None:
    """0 is an escape hatch from the WHOLE gate, not just the length floor —
    a job can legitimately end without terminal punctuation (a bare URL, a
    code fence, a table row, a bulleted list), so the truncation heuristic
    must be skippable too."""
    mod = _load_guard(overrides={"anomaly-hunt": 0})
    for text in ("ok", "Let me think about this..."):
        assert mod._cron_output_validity_guard(JOB, "/var/log/x.json", text, True) == text


def test_a_legitimate_silent_response_is_never_flagged() -> None:
    """A bare "[SILENT]" is 8 chars — well under the default floor — but
    upstream's own suppression check reads this exact deliver_content value
    right after this guard runs. Rewriting it here would turn a working
    quiet-run suppression into a false failure alarm on every quiet cycle."""
    mod = _load_guard()
    assert mod._cron_output_validity_guard(JOB, "/var/log/x.json", "[SILENT]", True) == "[SILENT]"


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
