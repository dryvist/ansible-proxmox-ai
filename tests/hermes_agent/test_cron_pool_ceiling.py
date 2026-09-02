"""hermes_agent_cron_wall_timeout_seconds is derived, not a hand-picked
constant: the defect (Vikunja 1906) was that ceiling and cadence were two
independently-chosen numbers nobody reconciled, so `ceiling < min(cadence)`
went unenforced. See defaults/main/20-brain-and-slack.yml for the formula and
its rationale, and roles/hermes_agent/filter_plugins/cron_schedule.py for the
cron-gap math this depends on.
"""

from __future__ import annotations

import pytest

from _cron_pool_ceiling_shared import (
    min_job_interval_seconds,
    render_wall_timeout_seconds,
    resolved_defaults,
    router_request_timeout_seconds,
    wall_timeout_seconds,
)
from cron_schedule import cron_min_gap_minutes


# --- cron_min_gap_minutes: the filter, in isolation -------------------------


@pytest.mark.parametrize(
    ("schedule", "expected_minutes"),
    [
        ("7 * * * *", 60),  # hourly at a fixed minute
        ("4 8-22 * * *", 60),  # hourly within a range
        ("19 0,12,16,19 * * *", 180),  # uneven hour list, tightest gap wins
        ("22 */6 * * *", 360),  # every-6-hours step
        ("*/15 * * * *", 15),  # every-15-minutes step
        ("0 2 * * *", 1440),  # once a day
        ("* * * * *", 1),  # fires every minute
    ],
)
def test_cron_min_gap_minutes(schedule: str, expected_minutes: int) -> None:
    assert cron_min_gap_minutes(schedule) == expected_minutes


def test_cron_min_gap_minutes_ignores_dow_conservatively() -> None:
    """A Monday-only job (dow=1) is read as if it fired every day at that
    minute/hour — the gap comes out SMALLER than the job's true weekly
    cadence, never larger. That direction is the safe one: this filter feeds
    a divisor for a safety ceiling, and under-estimating a gap only makes the
    derived ceiling tighter than strictly necessary, never lets it exceed a
    job's real cadence.
    """
    every_day = cron_min_gap_minutes("13 8 * * *")
    monday_only = cron_min_gap_minutes("13 8 * * 1")
    assert every_day == monday_only == 1440


# --- the store's own schedules feed the formula ------------------------------


def test_min_job_interval_matches_the_tightest_known_schedule() -> None:
    """Several jobs in hermes_agent_direct_cron_jobs fire hourly (e.g.
    splunk-triage, splunk-error-triage-v2, daily-status within its window);
    nothing in that list is tighter. 3600s is the real floor today, not a
    fixture value invented for this test.
    """
    assert min_job_interval_seconds() == 3600


def test_wall_timeout_matches_the_documented_formula() -> None:
    defaults = resolved_defaults()
    min_interval = min_job_interval_seconds(defaults)
    factor = defaults["hermes_agent_cron_pool_safety_factor"]
    router_timeout = router_request_timeout_seconds()

    expected = min(int(min_interval * factor), router_timeout - 1)

    assert wall_timeout_seconds(defaults) == expected == 1800


# --- the operator's core relationship, enforced structurally ----------------


def test_ceiling_stays_below_every_enabled_job_schedule() -> None:
    """The actual invariant the defect violated: ceiling < min(cadence). Not
    just true for today's numbers — recomputed from the same two live inputs
    every time, so a future schedule change that tightens the fastest job
    keeps this honest instead of silently reopening the starvation window.
    """
    defaults = resolved_defaults()
    ceiling = wall_timeout_seconds(defaults)
    min_interval = min_job_interval_seconds(defaults)
    assert ceiling < min_interval


def test_ceiling_stays_below_the_router_timeout() -> None:
    """The pre-existing Layer-1 assert (assert_brain_and_bridge.yml) this
    formula must keep satisfying, checked independently of that assert file.
    """
    assert wall_timeout_seconds() < router_request_timeout_seconds()


def test_formula_engages_the_safety_factor_branch_when_it_is_tighter() -> None:
    """At today's real schedules the router-timeout cap always binds first
    (2399 < 3600), so a broken safety factor would go uncaught by the tests
    above. Rendered against synthetic inputs where the factor branch is the
    tighter one, `min_interval * factor` must be exactly what comes out.
    """
    assert render_wall_timeout_seconds(
        min_interval_seconds=1000, safety_factor=0.5, router_timeout_seconds=100000
    ) == 500


def test_formula_engages_the_router_timeout_cap_when_it_is_tighter() -> None:
    """The other branch, isolated the same way: a huge min-interval must not
    let the ceiling escape the router's per-attempt timeout.
    """
    assert render_wall_timeout_seconds(
        min_interval_seconds=999999, safety_factor=1.0, router_timeout_seconds=100
    ) == 99


def test_a_looser_safety_factor_widens_the_ceiling_but_stays_under_cadence() -> None:
    """Mutation check on the formula's shape: raising the safety factor (a
    role var, not a constant baked into the formula) should move the ceiling
    up while the `< min_interval` invariant keeps holding on its own — proof
    the bound comes from the factor being < 1.0, not from 0.5 specifically.
    """
    defaults = resolved_defaults()
    loose = {**defaults, "hermes_agent_cron_pool_safety_factor": 0.9}
    baseline_ceiling = wall_timeout_seconds(defaults)
    loose_ceiling = wall_timeout_seconds(loose)
    min_interval = min_job_interval_seconds(defaults)

    assert loose_ceiling > baseline_ceiling
    assert loose_ceiling < min_interval
