"""ai-news moved from hermes to donna (operator decision, 2026-08-16).

An assistant-persona news digest should not run under the ops identity — and
without an explicit `identities:` override every job defaults to [hermes]
(hermes_agent_job_identities_default), so leaving it unset would have kept it
on hermes regardless of intent. This drives the real ownership expression
(hermes_agent_direct_cron_jobs_owned) through Jinja, rather than grepping the
YAML for the key, so a typo in the list value would still fail the test.
"""

from __future__ import annotations

import ast
from pathlib import Path

from jinja2 import Environment
from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"


def _owned_job_names(identity: str) -> set[str]:
    defaults = role_defaults(ROLE)
    env = Environment(autoescape=False)  # noqa: S701
    # `bool` (filter) and `contains` (test) are ansible-only, absent from bare
    # Jinja2. Inputs here are plain Python bool/list, so stdlib equivalents
    # are behaviourally identical for what this test exercises.
    env.filters["bool"] = bool
    env.tests["contains"] = lambda seq, item: item in seq
    ctx = dict(defaults)
    ctx["hermes_agent_id"] = identity
    # Computed directly rather than via .render(), which always returns a
    # str — piping that back through the `bool` filter stub above would
    # turn the literal string "False" into a truthy Python bool.
    ctx["hermes_agent_ops_workload_enabled"] = (
        identity in defaults["hermes_agent_job_identities_default"]
    )
    owned = env.from_string(defaults["hermes_agent_direct_cron_jobs_owned"]).render(
        ctx
    )
    # Jinja renders the list's Python repr; ai-news' name is itself a Jinja
    # expression ("{{ hermes_agent_ai_news_cron_name }}") resolved against the
    # same defaults, matching how the role actually renders job names.
    return {
        env.from_string(job["name"]).render(defaults)
        for job in ast.literal_eval(owned)
    }


def test_ai_news_is_owned_by_donna_not_hermes() -> None:
    assert "ai-news" in _owned_job_names("donna")
    assert "ai-news" not in _owned_job_names("hermes")


def test_assistant_jobs_still_hermes_free() -> None:
    """Regression guard: this change must not touch the existing split."""
    hermes_owned = _owned_job_names("hermes")
    donna_owned = _owned_job_names("donna")
    for assistant_job in (
        "assistant-daily-brief",
        "assistant-decision-nudge",
        "assistant-task-triage",
    ):
        assert assistant_job in donna_owned
        assert assistant_job not in hermes_owned
    # Ops-identity jobs with no explicit identities: are untouched by this change.
    assert "splunk-triage" in hermes_owned
    assert "splunk-triage" not in donna_owned
