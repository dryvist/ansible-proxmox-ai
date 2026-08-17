"""Safety contract for the Hermes brain watchdog: probe deadline, saturation
classification, and fleet reconcile.

Split from test_brain_watchdog_contract.py to stay under the token budget —
see _brain_watchdog_shared.py for the loaded template/defaults fixtures and
test_brain_watchdog_debounce.py for the debounce/flap-coalescing/probe-
readback contracts this leaves behind.

These are static assertions against the template text, matching the style used
by test_alert_routing.py and test_goal_mode_contract.py. They pin the
PROPERTIES that make enabling safe, not the exact numbers — the numbers are
role variables and an operator may tune them.
"""

import re

from _brain_watchdog_shared import ALL_VARS, DEFAULTS, REPO_ROOT, ROLE, ROUTER_DEFAULTS, WATCHDOG, _int_var


def test_probe_deadline_exceeds_the_router_rate_limit_backoff() -> None:
    """A probe deadline below the router's 429 backoff can never see a retry.

    The single-slot serving backend returns in well under a second when the
    slot is free; when it is busy the router takes a 429 and sleeps
    ai_router_retry_after_seconds before retrying. There is no outcome in
    between, so a deadline under that sleep measures "is the slot free right
    now" rather than "can the brain serve".

    Saturation no longer produces a DOWN edge regardless (probe_state classifies
    429 and curl rc 28 as busy), but the deadline still has to outlive one
    backoff or the probe can never observe a successful retry at all.
    """
    retry_after = _int_var(
        r"^ai_router_retry_after_seconds:\s*(\d+)", ALL_VARS, "ai_router_retry_after_seconds"
    )
    probe_timeout = _int_var(
        r"^hermes_agent_brain_watchdog_probe_timeout:\s*(\d+)",
        DEFAULTS,
        "hermes_agent_brain_watchdog_probe_timeout",
    )
    interval = _int_var(
        r'^hermes_agent_brain_watchdog_interval:\s*"(\d+)s"',
        DEFAULTS,
        "hermes_agent_brain_watchdog_interval",
    )
    # The router must consume the shared constant, not re-declare a literal —
    # otherwise this assertion guards a value the router no longer uses.
    assert "llm_router_retry_after_seconds: \"{{ ai_router_retry_after_seconds }}\"" in (
        ROUTER_DEFAULTS
    ), "llm_router_retry_after_seconds must derive from the all.yml constant this test reads"
    assert probe_timeout > retry_after, (
        f"probe timeout {probe_timeout}s must exceed the router's {retry_after}s retry_after"
    )
    assert probe_timeout < interval, (
        f"probe timeout {probe_timeout}s must stay under the {interval}s interval so probes "
        "cannot overlap"
    )


def test_queue_recovery_expects_the_timer_active_after_normal_operation() -> None:
    """The watchdog being enabled by default means every playbook that stops it
    for queue recovery must bring it back when that window ends — otherwise
    the first maintenance operation silently
    re-disables auto-pause/resume for good, with no alert that it happened.
    """
    recover = (REPO_ROOT / "playbooks" / "recover-hermes-queue.yml").read_text()

    assert "ActiveState == 'active'" in recover
    assert "ActiveState == 'inactive'" not in recover


def test_saturation_is_classified_busy_not_down() -> None:
    """THE 2026-08-05 FALSE-OUTAGE FIX.

    The serving host admits one request at a time and rejects the rest with an
    instant 429 (measured at 18.8us — a live server declining work). Over
    2026-08-01..08-05, 57% of all gate requests were 429 and the watchdog scored
    every one as DOWN, so ordinary contention paused the whole cron fleet and
    paged hourly. Meanwhile the real work path absorbs exactly this for ~320s
    (rate_limit_retries x retry_after), so the watchdog was declaring outages in
    conditions where every job it protects succeeds.

    curl rc 28 is the same condition seen from the other side: the router sleeps
    retry_after between retries, so a saturated brain usually starves the probe
    of a reply rather than handing it a 429.
    """
    assert '[[ "${code}" == "429" ]] && { printf \'busy\'; return; }' in WATCHDOG
    assert "(( rc == 28 )) && { printf 'busy'; return; }" in WATCHDOG
    assert "busy=$(( busy + 1 ))" in WATCHDOG


def test_non_timeout_curl_failures_still_classify_down() -> None:
    """rc 28 (our own deadline) is busy; every other curl failure is not.

    2026-08-06: confirmed there is no retry loop anywhere in this script (one
    curl call, no --retry, no bash retry, no systemd Restart=) — the single
    ai_router_retry_after_seconds-derived deadline in probe_timeout already IS
    "one timeout resolves to busy and returns". This pins the other half of
    that contract: a connection refused (7), a DNS failure (6), a TLS failure
    (35), or any other non-28 rc must still fall through to 'down' rather than
    being swallowed by the busy path — that's the difference between a
    saturated-but-alive backend and a genuinely unreachable one.
    """
    probe_fn = WATCHDOG.split("probe_state() {", 1)[1].split("\n}\n", 1)[0]
    assert (
        "if (( rc != 0 )); then\n"
        "    (( rc == 28 )) && { printf 'busy'; return; }\n"
        "    printf 'down'; return\n"
        "  fi" in probe_fn
    ), "a non-28 curl failure must fall through to printf 'down', not busy"


def test_busy_does_not_touch_the_down_or_up_streak() -> None:
    """Busy is neither evidence of failure nor evidence of recovery.

    If busy decremented the streak it would still pause the fleet, just slower.
    If it incremented it, a saturated brain would fake a recovery it never made.
    The busy branch must therefore leave `streak` alone entirely.
    """
    branch = WATCHDOG.split("  busy)", 1)[1].split("  *)", 1)[0]
    # Comments in this branch legitimately discuss streak/edges; assert on the
    # executable lines only, or the test pins prose instead of behaviour.
    code = "\n".join(
        line for line in branch.splitlines() if not line.lstrip().startswith("#")
    )
    assert "streak=" not in code, (
        "the busy branch must not assign streak — it is neither a failure nor a recovery"
    )
    assert "cron_do" not in code, "busy must never pause or resume the fleet"
    assert "handle_edge" not in code, "busy must never fire an alert edge"
    assert "hc_ping" not in code, (
        "busy must not ping the deadman — nothing proved the brain can generate"
    )


def test_sustained_saturation_still_escalates_to_down() -> None:
    """Busy must not become a permanent mute.

    A watchdog that can never report a saturated brain is worse than a noisy
    one: it converts a fix for false positives into a suppressed true positive.
    If nothing can serve a single 1-token probe for busy_grace_probes cycles,
    real jobs are failing too — the router itself gives up after
    rate_limit_retries x retry_after.
    """
    assert 'result="down"' in WATCHDOG, "sustained busy must escalate into the normal DOWN path"
    assert "BUSY_GRACE_PROBES > 0 && busy + 1 >= BUSY_GRACE_PROBES" in WATCHDOG

    grace = _int_var(
        r"^hermes_agent_brain_watchdog_busy_grace_probes:\s*(\d+)",
        DEFAULTS,
        "hermes_agent_brain_watchdog_busy_grace_probes",
    )
    interval = _int_var(
        r'^hermes_agent_brain_watchdog_interval:\s*"(\d+)s"',
        DEFAULTS,
        "hermes_agent_brain_watchdog_interval",
    )
    retry_after = _int_var(
        r"^ai_router_retry_after_seconds:\s*(\d+)", ALL_VARS, "ai_router_retry_after_seconds"
    )
    rate_limit_retries = _int_var(
        r"^llm_router_rate_limit_retries:\s*(\d+)",
        ROUTER_DEFAULTS,
        "llm_router_rate_limit_retries",
    )

    assert grace > 0, "a zero grace disables saturation reporting entirely"
    # Must outlast the router's own tolerance, or the watchdog pages for a
    # backlog the request path would still have ridden out.
    router_tolerance = retry_after * rate_limit_retries
    assert grace * interval > router_tolerance, (
        f"busy grace {grace * interval}s must exceed the router's own {router_tolerance}s "
        "429 tolerance, else the watchdog pages for saturation real jobs survive"
    )


def test_probe_asks_for_the_cheapest_reply_that_still_proves_generation() -> None:
    """The probe competes for the same single slot as real work, so it asks for
    the smallest reply that still proves the engine generated: max_tokens 1
    finishes on "length" (covered by the accepted finish_reason set) and still
    yields completion_tokens >= 1, which catches a wedged engine reporting zero.
    """
    assert '\\"max_tokens\\":1' in WATCHDOG


def test_a_paused_fleet_is_reconciled_not_only_edge_resumed() -> None:
    """THE INDEFINITE-PAUSE HOLE.

    The DOWN branch pauses the fleet and persists the state in two separate
    steps. A kill between them leaves every brain-dependent job paused while the
    state file still reads "up", and the resume is gated on state == "down" — so
    it never fires. The fleet then stays paused until some future outage happens
    to complete a whole down->up cycle, which may never come.

    """
    assert "reconcile_fleet" in WATCHDOG, "a healthy brain must converge the fleet to running"
    # It must run on the healthy path when NO edge fired — that is the stuck case.
    up_branch = WATCHDOG.split("  up)", 1)[1].split("  busy)", 1)[0]
    assert "reconcile_fleet" in up_branch
    assert "else" in up_branch, "reconcile must run when the up-edge branch did NOT fire"


def test_reconcile_is_silent_and_only_touches_the_seeded_fleet() -> None:
    """Repair is not an incident.

    Paging on a reconcile would re-create exactly the alert noise this watchdog
    exists to suppress, and reconciling jobs the watchdog does not own would
    undo a human's deliberate pause.
    """
    body = WATCHDOG.split("reconcile_fleet() {", 1)[1].split("\n}", 1)[0]
    assert "SEEDED_JOBS" in body, "reconcile must be scoped to the fleet the watchdog owns"
    assert "logger" in body, "reconcile must leave an audit trail"
    for noisy in ("slack_post", "ntfy", "handle_edge"):
        assert noisy not in body, f"reconcile must not {noisy} — repair is not an incident"


def test_reconcile_cannot_be_disabled_by_default() -> None:
    """Disabling it re-opens the indefinite pause, so the default must be on."""
    match = re.search(
        r"^hermes_agent_brain_watchdog_reconcile_paused:\s*(\w+)", DEFAULTS, re.M
    )
    assert match is not None, "the reconcile toggle must exist as a role default"
    assert match.group(1) == "true"


def test_the_converge_resume_claim_is_not_reintroduced() -> None:
    """The old comment claimed a converge re-resumes a paused fleet. It does not:
    the role's tasks contain no `cron resume` at all. Pin the correction so the
    false reassurance cannot come back and mask the hole again.
    """
    tasks = (ROLE / "tasks" / "main.yml").read_text()
    assert "cron resume" not in tasks.replace("# ", ""), (
        "if a real `cron resume` task was added, update this test and the watchdog comment"
    )
    assert "converge re-resumes" not in WATCHDOG


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
