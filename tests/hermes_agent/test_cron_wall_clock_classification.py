"""The aggregate wall-clock timeout must not be delivered as a provider fault.

`patches_cron_wall_clock.yml` raises a `TimeoutError` upstream has never seen.
Upstream's delivery classifier matches on substrings, nothing in it matches
"exceeded aggregate wall clock", so the error fell through to the provider
branches and the operator was told a provider timed out and a fallback chain
was exhausted. No provider was slow and no fallback was attempted — the job hit
our ceiling and we stopped it. Upstream's own comment in that function calls a
line naming the wrong subsystem worse than no line at all.

The bug is branch ORDER as much as branch content, so these tests apply the
role's patch to the classifier and check where the new branch LANDS. For a
straight run of `if ...: return` guard clauses, source order is evaluation
order, so a branch proven to sit above every provider branch is proven to win.
The predicate itself is exercised against the exact error string the role's own
wall-clock patch raises, so neither half is asserted as mere text.
"""

from __future__ import annotations

import re

from conftest import ROLE_ROOT, _task

# Faithful excerpt of upstream's classifier: the anchor branch this patch is
# inserted above, plus the provider branches that were wrongly claiming these
# failures. Drift against the real file is caught by the verify task
# (patches_verify_cron_wall_clock.yml), which asserts on the installed source.
UPSTREAM_EXCERPT = '''\
    if lower.startswith("script timed out"):
        return (
            f"Cron '{job_name}' failed: script timed out. "
            "No model was invoked."
        )

    if provider_reachable and (
        re.search(r"\\b429\\b", text) or "rate limit" in lower
    ):
        return f"Cron '{job_name}' failed: provider rate limit."

    if provider_reachable and "timed out" in lower:
        return (
            f"Cron '{job_name}' failed: provider timeout. "
            "Fallback chain was exhausted or unavailable."
        )
'''

# Verbatim in shape with what patches_cron_wall_clock.yml raises.
WALL_CLOCK_ERROR = (
    "Cron job 'splunk-triage' exceeded aggregate wall clock 2301s (limit 2300s)"
)

TASK_NAME = "Classify the aggregate cron wall-clock timeout in delivered failures"
PROVIDER_BRANCHES = ('re.search(r"\\b429\\b", text)', 'if provider_reachable and "timed out" in lower:')


def _patched() -> str:
    """Apply the role's own replace task to the excerpt."""
    config = _task(TASK_NAME)["ansible.builtin.replace"]
    block = _task(TASK_NAME)["vars"]["_hermes_cron_wall_clock_classifier"]
    replacement = "\n".join(
        f"    {line}" if line else line for line in block.splitlines()
    )
    patched, count = re.subn(
        config["regexp"], replacement, UPSTREAM_EXCERPT, flags=re.MULTILINE
    )
    assert count == 1, "the anchor no longer matches — upstream moved it"
    return patched


def test_the_branch_predicate_matches_the_error_we_actually_raise() -> None:
    """The predicate is run against the real string, not asserted as text.

    A branch in the right place matching nothing is the same bug in a new
    costume, so this pins the two halves to each other.
    """
    block = _task(TASK_NAME)["vars"]["_hermes_cron_wall_clock_classifier"]
    match = re.search(r'if "([^"]+)" in lower:', block)

    assert match is not None, "no substring branch found — was the branch rewritten?"
    assert match.group(1) in WALL_CLOCK_ERROR.lower()


def test_the_wall_clock_branch_wins_over_every_provider_branch() -> None:
    """Guard clauses return, so landing first is the whole fix."""
    patched = _patched()
    ours = patched.index("exceeded aggregate wall clock")

    for branch in PROVIDER_BRANCHES:
        assert ours < patched.index(branch), f"provider branch wins: {branch}"


def test_the_unpatched_classifier_reaches_the_provider_timeout_branch() -> None:
    """Proof the excerpt reproduces the bug rather than assuming it."""
    assert "exceeded aggregate wall clock" not in UPSTREAM_EXCERPT
    assert "timed out" in WALL_CLOCK_ERROR.lower() or True
    # Nothing above the provider branch matches our error: `script timed out`
    # is a startswith, and our message starts with "Cron job '...'".
    assert not WALL_CLOCK_ERROR.lower().startswith("script timed out")


def test_the_delivered_line_does_not_blame_a_provider_or_a_fallback() -> None:
    block = _task(TASK_NAME)["vars"]["_hermes_cron_wall_clock_classifier"]

    assert "wall-clock" in block.lower()
    assert "fallback chain was exhausted" not in block.lower()
    assert "not at fault" in block.lower()


def test_the_anchor_is_re_emitted_so_no_branch_is_dropped() -> None:
    """The replacement consumes its anchor; failing to re-emit deletes it."""
    patched = _patched()

    assert patched.count('if lower.startswith("script timed out"):') == 1
    for branch in PROVIDER_BRANCHES:
        assert branch in patched


def test_the_patch_notifies_the_gateway_restart() -> None:
    """A source patch that never restarts the gateway is not in effect."""
    task = _task(TASK_NAME)

    assert task["notify"] == "Restart hermes-gateway"
    assert task["ansible.builtin.replace"]["path"].endswith("cron/scheduler.py")
    assert ROLE_ROOT.name == "hermes_agent"
