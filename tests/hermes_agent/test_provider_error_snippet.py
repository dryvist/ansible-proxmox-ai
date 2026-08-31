"""A fallback cause must survive the provider-error snippet bound.

The formatter clips its snippet before the agent returns the error, so a cron
failure is stored already truncated. The cut lands at a content-dependent
position, which is why the same failure class survives or dies depending on how
long its first line happened to be.

Both fixtures below are VERBATIM from the job store — the surviving variant and
the destroyed one, same failure class, same day. Nothing here reconstructs an
untruncated original, because we do not have one; the arithmetic runs on what
was actually measured.
"""

from __future__ import annotations

import re

import yaml

from conftest import ROLE_ROOT, _task

TASK_NAME = "Widen the provider-error snippet so a fallback cause is not cut in half"

# The stored string is the exception class name, the HTTP prefix, then the
# clipped snippet: 14 + 10 + 300 = 324, which is what every chain-exhaustion
# record on the guest measured.
STORED_PREFIX_LEN = len("RuntimeError: ") + len("HTTP 429: ")
REJECTED_BOUND = 300

# Verbatim, both observed. The only difference is how long each record's first
# line was — the same cause, cut in two different places.
CAUSE_SURVIVED = "No deployments available - crossed budget: Exceeded budget"
CAUSE_DESTROYED = "No deployments available - c"
CAUSE_CLIPPED = "litellm.ContextWindowExceede"


def _shipped_bound() -> int:
    """The bound the role's replace task installs."""
    replace = _task(TASK_NAME)["ansible.builtin.replace"]["replace"]
    match = re.search(r"\[:(\d+)\]", replace)
    assert match is not None, "the replacement no longer carries a slice bound"
    return int(match.group(1))


def test_the_rejected_bound_destroys_the_budget_diagnosis() -> None:
    """Real data, not a hypothesis: the delivered line named no cause at all."""
    assert "budget" not in CAUSE_DESTROYED
    assert "budget" in CAUSE_SURVIVED, "same class, same day, different cut point"


def test_the_shipped_bound_covers_what_the_rejected_one_cut() -> None:
    """The headroom must exceed the characters the destroyed variant lost.

    This is the test that makes 300 and 500 disagree: at the rejected bound the
    headroom is zero by definition, so it can never cover the shortfall.
    """
    headroom = _shipped_bound() - REJECTED_BOUND
    shortfall = len(CAUSE_SURVIVED) - len(CAUSE_DESTROYED)

    assert headroom > 0, "the patch must widen the bound, not restate it"
    assert headroom > shortfall, (
        f"{headroom} more characters does not cover the {shortfall} the "
        "destroyed variant lost"
    )


def test_the_shipped_bound_also_covers_the_clipped_context_window_cause() -> None:
    """The other arm lost one letter; the headroom must cover that too."""
    headroom = _shipped_bound() - REJECTED_BOUND

    assert headroom > len("litellm.ContextWindowExceededError") - len(CAUSE_CLIPPED)


def test_the_stored_length_matches_what_the_guest_showed() -> None:
    """Pins the arithmetic the patch's reasoning rests on: 14 + 10 + 300 = 324."""
    assert STORED_PREFIX_LEN + REJECTED_BOUND == 324


def test_the_patch_asserts_the_old_bound_is_gone_not_merely_that_ours_is_present() -> None:
    """`replace` reports ok on a non-match; a partial apply must fail loudly."""
    tasks = yaml.safe_load(
        (ROLE_ROOT / "tasks" / "patches_provider_error_snippet.yml").read_text()
    )
    assertion = next(t for t in tasks if "ansible.builtin.assert" in t)
    conditions = " ".join(assertion["ansible.builtin.assert"]["that"])

    assert "'[:300]' not in" in conditions
    assert "notify" in _task(TASK_NAME), "a source patch must restart the gateway"


def test_the_patch_is_included_in_the_role() -> None:
    """A patch file nothing includes is a file that never runs."""
    assert "patches_provider_error_snippet.yml" in (
        ROLE_ROOT / "tasks" / "main.yml"
    ).read_text()
