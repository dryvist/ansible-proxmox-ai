"""Safety contract for the Hermes brain watchdog.

WHY THIS EXISTS. `hermes_agent_brain_watchdog_enabled` was flipped to true
2026-08-01 (a Caddy llm-gate crash that took the primary AND fallback serving
legs down at once, twice in one night, with the seeded cron fleet firing into
a dead brain the whole time — exactly the gap this watchdog closes). Before
that happened, the watchdog's decision logic needed a contract, because it
does not merely alert: it `pause`/`resume`s the role-seeded cron fleet. A
watchdog that pauses the whole fleet on a single unlucky probe would silently
stop all Hermes work, and the symptom — nothing running — looks identical to
an idle board.

The template already implements debounce, flap coalescing and a sustained-flap
escalation. Nothing pinned them. `test_queue_recovery_contract.py` does touch
this template, but only to assert the ALERT TEXT is honest (that it reports
command outcomes and disclaims queue health). The logic that decides whether
to pause the fleet at all — the streak debounce and the flap coalescing — had
no coverage. That gap is what this file closes; the alert-honesty assertions
are deliberately left where they already live rather than duplicated here.

These are static assertions against the template text, matching the style used
by test_alert_routing.py and test_goal_mode_contract.py. They pin the
PROPERTIES that make enabling safe, not the exact numbers — the numbers are
role variables and an operator may tune them.
"""

import re
from pathlib import Path

from _role_files import role_defaults_text

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
REPO_ROOT = ROLE.parents[1]
WATCHDOG = (ROLE / "templates" / "hermes-brain-watchdog.sh.j2").read_text()
DEFAULTS = role_defaults_text(ROLE)
ROUTER_DEFAULTS = role_defaults_text(REPO_ROOT / "roles" / "llm_router")
ALL_VARS = (REPO_ROOT / "inventory" / "group_vars" / "all.yml").read_text()


def _int_var(pattern: str, haystack: str, what: str) -> int:
    """Extract one integer setting, failing loudly when the key is gone.

    A bare `re.search(...).group(1)` raises AttributeError on a renamed or
    removed key, which reads as a broken test rather than a broken contract.
    """
    match = re.search(pattern, haystack, re.M)
    assert match is not None, f"could not find {what} — was it renamed or templated away?"
    return int(match.group(1))


def test_both_edge_thresholds_come_from_role_vars_not_literals() -> None:
    """A hardcoded threshold cannot be tuned per host and drifts from the docs."""
    assert 'DOWN_AFTER="{{ hermes_agent_brain_watchdog_down_after }}"' in WATCHDOG
    assert 'UP_AFTER="{{ hermes_agent_brain_watchdog_up_after }}"' in WATCHDOG
    assert "hermes_agent_brain_watchdog_down_after:" in DEFAULTS
    assert "hermes_agent_brain_watchdog_up_after:" in DEFAULTS


def test_pausing_the_fleet_requires_a_debounced_down_edge() -> None:
    """THE PROPERTY THAT MATTERS MOST.

    The down edge — the one that pauses every seeded cron — must be gated on a
    run of consecutive failures reaching DOWN_AFTER, never on one probe. If
    this regresses into a bare `if probe_failed`, a single timeout takes the
    whole fleet down.
    """
    assert '[[ "${state}" == "up" ]] && (( streak <= -DOWN_AFTER ))' in WATCHDOG
    assert '[[ "${state}" == "down" ]] && (( streak >= UP_AFTER ))' in WATCHDOG


def test_a_flipped_probe_result_resets_the_streak() -> None:
    """Alternating probes must never accumulate into an edge.

    Without the reset, ok/fail/ok/fail would walk a counter to the threshold
    and page (and pause) on a link that is merely noisy rather than down.
    """
    # failure after any run of successes restarts the negative run at -1
    assert "if (( streak > 0 )); then streak=-1; else streak=$(( streak - 1 )); fi" in WATCHDOG
    # success after any run of failures restarts the positive run at 1
    assert "if (( streak < 0 )); then streak=1; else streak=$(( streak + 1 )); fi" in WATCHDOG


def test_sustained_flap_escalation_is_disableable() -> None:
    """0 must switch the loud escalation off cleanly, matching the repo's
    established "0 = off" convention for optional rungs."""
    assert "(( SUSTAINED_FLAP_THRESHOLD > 0 )) || return 0" in WATCHDOG


def test_flap_coalescing_state_is_persisted() -> None:
    """Coalescing across invocations needs on-disk state; the timer runs the
    script fresh each interval, so in-memory counters would reset every tick
    and every edge would page."""
    for marker in ("flap_cooldown_until", "flap_count", "flap_since"):
        assert marker in WATCHDOG, f"missing flap state file: {marker}"


def test_a_flap_episode_is_not_cleared_while_the_brain_is_still_down() -> None:
    """THE PROPERTY THAT KEEPS ONE OUTAGE TO ONE MESSAGE PAIR.

    flap_summary_check runs every invocation and used to clear the whole
    episode — cooldown window, flap count, sustained-page re-arm — as soon as
    the cooldown timer elapsed, regardless of state. During a wedge whose down
    phase outlasts the cooldown that disarmed every gate mid-outage: the next
    edge was no longer inside a cooldown, so it alerted as a fresh outage, and
    the episode repeated one down + one up message per flap cycle for hours.

    An episode ends when the brain is UP. While it is down the window must be
    extended and nothing cleared, so the `rm -f` must be unreachable until the
    state is up.
    """
    body = WATCHDOG.split("flap_summary_check() {")[1].split("\n}")[0]

    guard = body.index('if [[ "${state}" != "up" ]]; then')
    extend = body.index('> "${FLAP_COOLDOWN_FILE}"', guard)
    early_return = body.index("return 0", extend)
    clear = body.index("rm -f", guard)

    assert extend < early_return < clear, \
        "while down, the cooldown window must be extended and the function must " \
        "return before anything is cleared"
    assert "${SUSTAINED_REARM_FILE}" in body[clear:], \
        "the sustained-page re-arm is episode state and must be cleared only on recovery"


def test_recovery_alert_disclaims_queue_verification() -> None:
    """The DOWN-edge message's "does not verify Kanban queue health" disclaimer
    is already pinned by test_queue_recovery_contract. Its UP-edge counterpart
    is not, and recovery is the riskier of the two to overclaim: a green
    "recovered" that implies the queue is draining invites the operator to
    stop looking."""
    assert "still requires queue verification" in WATCHDOG


def test_watchdog_is_enabled_by_default() -> None:
    """Regression guard for the 2026-08-01 flip. A silent revert back to false
    would leave the fleet with no auto-pause the next time the brain drops —
    exactly the incident that prompted enabling it."""
    assert "hermes_agent_brain_watchdog_enabled: true" in DEFAULTS


def test_probe_is_a_real_completion_not_a_liveness_endpoint() -> None:
    """THE TRAP THIS WATCHDOG EXISTS TO AVOID.

    A probe against /v1/models or any bare health endpoint can return 200 while
    the engine is wedged and generating nothing — confirmed live 2026-07-16,
    where a broken batch scheduler kept answering HTTP 200 with
    finish_reason=error and zero tokens. The probe must hit chat/completions
    with a real message body and verify BOTH a real finish_reason AND a
    nonzero completion_tokens count, not just a 200 status code.
    """
    assert 'PROBE_URL="${BASE_URL}/chat/completions"' in WATCHDOG
    assert '\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"ping\\"}]' in WATCHDOG
    assert '"finish_reason"' in WATCHDOG
    assert '"completion_tokens"' in WATCHDOG
    # A bare HTTP-200 check without the body assertions would silently pass a
    # wedged engine — the probe must require both.
    assert '[[ "${code}" == "200" ]] \\' in WATCHDOG


def test_pause_outcome_is_read_back_off_the_board_not_from_a_return_code() -> None:
    """THE FALSE-SUCCESS GUARD.

    `hermes cron pause <missing>` prints "Job ... not found" and still exits 0.
    A loop that scores the return code therefore counts a name with no job
    behind it as a pause: 8 of the 21 names carried no job, so every alert
    reported 21 pauses over a fleet of 13. The outcome must come from the
    board — what the job's state actually IS — never from what the command
    returned.
    """
    assert 'board="$("${HERMES_BIN}" cron list --all 2>/dev/null)"' in WATCHDOG
    # the pause/resume call is fired for effect only; its status is discarded
    assert '"${HERMES_BIN}" cron "${verb}" "${job}" >/dev/null 2>&1 || true' in WATCHDOG
    # ...and the counters are driven by the board lookup that follows
    assert 'grep -B1 -E "^ *Name: +${job}\\$" <<< "${board}"' in WATCHDOG


def test_board_readback_includes_paused_jobs() -> None:
    """`cron list` WITHOUT --all omits paused jobs entirely.

    Drop the flag and every pause verifies as a failure (the job it just paused
    has vanished from the output), and a paused job becomes indistinguishable
    from a deleted one. This one flag is what makes the readback above mean
    anything.
    """
    assert "cron list --all" in WATCHDOG
    assert 'want="paused"' in WATCHDOG


def test_watchdog_pauses_the_same_fleet_a_cluster_window_pauses() -> None:
    """hermes_agent_seeded_cron_names covers only the script-fed `-enqueue`
    crons, which never call the brain. The enabled `*-v2` jobs that DO call it
    live in hermes_agent_direct_cron_jobs, so pausing the seeded list alone
    left every brain-dependent job firing into a dead brain — the same gap
    found on the cluster-pause path 2026-07-25. Both paths must derive from
    one list so they cannot drift apart again.
    """
    assert "{{ hermes_agent_cluster_pause_cron_names | length }}" in WATCHDOG
    assert "{% for job in hermes_agent_cluster_pause_cron_names %}" in WATCHDOG


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


def test_maintenance_playbooks_expect_the_timer_active_after_normal_operation() -> None:
    """The watchdog being enabled by default means every playbook that stops it
    for a deliberate window (cluster pause, queue recovery) must bring it back
    when that window ends — otherwise the very first maintenance op silently
    re-disables auto-pause/resume for good, with no alert that it happened.

    cluster-hermes-pause.yml legitimately stops the timer and stays that way
    (it's reversed by resume, not by itself) so it is deliberately not covered
    here.
    """
    resume = (REPO_ROOT / "playbooks" / "cluster-hermes-resume.yml").read_text()
    recover = (REPO_ROOT / "playbooks" / "recover-hermes-queue.yml").read_text()

    assert "enabled: true" in resume
    assert "state: started" in resume
    assert "ActiveState == 'inactive'" not in resume

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

    cluster-hermes-pause.yml reaches the identical end state by design, because
    it pauses via ansible and never writes the state file at all.
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
