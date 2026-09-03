"""Remediation advice delivered on a cron failure must be actionable here.

Two of upstream's remedies are correct for a stock deployment and wrong for
this one, so both are overridden rather than disabled:

* the no-fallback-chain branch tells the operator to add a chain to the agent's
  own config. Our fallbacks live in the router in front of the agent, which the
  agent cannot see — following the advice would add a second, competing chain.
* the repeated-failure nudge offers pausing alongside fixing. A paused job is
  invisible (no runs, no failures, no alerts), so the cheapest response to a
  noisy job is the one that hides it permanently.

`replace` reports `ok` when its pattern does not match, so the thing worth
testing is that each regexp still matches the text it claims to — these apply
the role's own patterns to verbatim upstream excerpts and require exactly one
substitution. Drift in the installed file is caught separately by
patches_verify_cron_wall_clock.yml.
"""

from __future__ import annotations

import re

from jinja2 import Environment

from conftest import _task

# Verbatim from cron/scheduler.py — the two remedies being overridden.
UPSTREAM_FALLBACK_BRANCH = '''\
    return (
        "No fallback chain configured — add one with `hermes fallback add`, "
        "or set a cron fleet default via `cron.model` + `cron.model_provider` "
        "in config.yaml."
    )
'''

UPSTREAM_NUDGE = '''\
    return (
        f"\\nThis job has failed {streak} runs in a row — worth a review. "
        f"Fix its prompt/config, or pause it with `hermes cron pause {job_ref}` "
        "(resume/remove also available) to stop the noise."
    )
'''

FALLBACK_TASK = "Correct the fallback-chain advice for router-fronted deployments"
NUDGE_TASK = "Drop the pause suggestion from the repeated-failure nudge"


def _apply(task_name: str, var_name: str, indent: int, source: str) -> str:
    """Run the role's replace exactly as Ansible would, filter included."""
    task = _task(task_name)
    block = task["vars"][var_name]
    rendered = Environment(autoescape=False).from_string(
        "{{ block | indent(%d, true) }}" % indent
    ).render(block=block)
    patched, count = re.subn(
        task["ansible.builtin.replace"]["regexp"],
        rendered,
        source,
        flags=re.MULTILINE,
    )
    assert count == 1, f"{task_name}: pattern matched {count} times, expected 1"
    return patched


def test_the_fallback_advice_points_at_the_router_not_the_agent_config() -> None:
    patched = _apply(FALLBACK_TASK, "_hermes_cron_fallback_advice", 4,
                     UPSTREAM_FALLBACK_BRANCH)

    assert "hermes fallback add" not in patched
    assert "router" in patched.lower()


def test_the_replaced_fallback_branch_is_still_valid_indented_python() -> None:
    """The indent filter is load-bearing: a flush-left return breaks the file."""
    patched = _apply(FALLBACK_TASK, "_hermes_cron_fallback_advice", 4,
                     UPSTREAM_FALLBACK_BRANCH)

    assert patched.startswith("    return ("), patched[:40]
    for line in patched.splitlines():
        assert not line or line.startswith("    "), f"lost indent: {line!r}"


def test_the_nudge_keeps_the_streak_and_drops_the_pause_instruction() -> None:
    patched = _apply(NUDGE_TASK, "_hermes_cron_nudge_remedy", 8, UPSTREAM_NUDGE)

    assert "failed {streak} runs in a row" in patched, "the useful half must stay"
    assert "hermes cron pause" not in patched
    assert "hides the failures" in patched


def test_the_replaced_nudge_is_still_valid_indented_python() -> None:
    patched = _apply(NUDGE_TASK, "_hermes_cron_nudge_remedy", 8, UPSTREAM_NUDGE)

    for line in patched.splitlines():
        assert not line or line.startswith("    "), f"lost indent: {line!r}"
    assert patched.rstrip().endswith(")")


def test_both_patches_notify_the_gateway_restart() -> None:
    """A source patch that never restarts the gateway is not in effect."""
    for name in (FALLBACK_TASK, NUDGE_TASK):
        task = _task(name)
        assert task["notify"] == "Restart hermes-gateway", name
        assert task["ansible.builtin.replace"]["path"].endswith("cron/scheduler.py")
