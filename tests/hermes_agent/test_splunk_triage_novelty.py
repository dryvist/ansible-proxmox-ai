"""Self-check for the Splunk triage per-day novelty contract.

Split from test_splunk_triage.py to stay under the token budget — see
_splunk_triage_shared.py for the loaded template/guard fixtures,
test_splunk_triage_guard.py for the markup-guard and job-config contract, and
test_splunk_triage_ladder.py for the tier 2/3 escalation ladder this leaves
behind.

Per-day novelty: a steady stream is presented ONCE per UTC day. Findings are
deltas against the PREVIOUS run, so the baseline advances and no finding
restates itself; `critical` means "bypass the ledger", which shows up when
the same delta RECURS within a day, not as a line repeating every run. Never
fabricate: zero rows and an absent baseline are stated as themselves.

Runs bare (`python3 tests/hermes_agent/test_splunk_triage_novelty.py`) or
under pytest. Plain asserts, no fixtures, no framework.
"""
from _splunk_triage_shared import GUARD, JOB, OTLP, RAFT, TMPMOUNT, TRIAGE, at, rows


def test_the_report_leads_with_the_fault_not_the_host():
    """The defect this replaces: "openbao-02 / syslog — 18.0k events (was 17.9k)"
    told the operator a counter moved and nothing about what was wrong."""
    text, _ = TRIAGE.build_report(rows({RAFT: {"openbao-02": 3520}}), at(1), None)
    body = text.splitlines()[1]
    assert "storage.raft: failed to heartbeat" in body, \
        "the first thing on a finding line must be the error signature"
    assert body.index("storage.raft") < text.index("openbao-02"), \
        "the fault leads; the host follows it"
    assert "3.5k events" in text and "openbao-02" in text


def test_first_run_reports_counts_and_claims_no_change():
    text, state = TRIAGE.build_report(rows({RAFT: {"openbao-02": 400}}), at(1), None)
    assert "storage.raft" in text and "400" in text
    assert "NEW" not in text and "ESCALATING" not in text, \
        "nothing can be new against an absent baseline"
    assert state["counts"] == {TRIAGE.sig_key(RAFT): 400}
    assert state["hosts"] == {"openbao-02": 400}


def test_one_fault_across_many_hosts_is_one_finding():
    """The fleet-wide tmp.mount failure is one defect on 30 machines. Grouping by
    host printed it 30 times and buried the singleton faults that matter."""
    text, _ = TRIAGE.build_report(
        rows({TMPMOUNT: {f"host{i:02d}": 50 for i in range(30)}}), at(1), None)
    assert text.count("tmp.mount") == 1, "one signature is one line"
    assert "30 hosts" in text, "but the blast radius must still be visible"
    assert "(+27 more)" in text, "and the name cap must be honest"


def test_steady_signature_is_presented_once_per_day():
    data = rows({RAFT: {"openbao-02": 400}})
    text, state = TRIAGE.build_report(data, at(1), None)
    assert "storage.raft" in text, "the day's first run presents the signature"
    text, state = TRIAGE.build_report(data, at(2), state)
    assert "storage.raft" not in text, "a steady signature must not repeat within the day"
    assert "No new signatures" in text, "the day gets one exhausted-search line"
    assert "1 error signature(s)" in text, "and it names the space it covered"
    text, _ = TRIAGE.build_report(data, at(3), state)
    assert text == TRIAGE.SILENT, "after that, silence for the rest of the day"


def test_a_new_utc_day_resets_the_ledger():
    data = rows({RAFT: {"openbao-02": 400}})
    _, state = TRIAGE.build_report(data, at(1), None)
    _, state = TRIAGE.build_report(data, at(2), state)
    text, state = TRIAGE.build_report(data, at(3), state)
    assert text == TRIAGE.SILENT
    text, _ = TRIAGE.build_report(data, at(1, day=25), state)
    assert "storage.raft" in text, "a new UTC day re-presents routine information"


def test_a_new_signature_is_flagged_then_tracked_as_steady():
    _, state = TRIAGE.build_report(rows({RAFT: {"openbao-02": 400}}), at(1), None)
    both = rows({RAFT: {"openbao-02": 400}, OTLP: {"open-webui": 90}})
    text, state = TRIAGE.build_report(both, at(2), state)
    assert "*NEW*" in text and "trace_exporter" in text
    text, state = TRIAGE.build_report(both, at(3), state)
    assert "trace_exporter" in text and "*NEW*" not in text, \
        "once the baseline knows it, it is a steady signature, not a new one"


def test_an_order_of_magnitude_climb_is_escalating():
    _, state = TRIAGE.build_report(rows({OTLP: {"open-webui": 90}}), at(1), None)
    text, _ = TRIAGE.build_report(rows({OTLP: {"open-webui": 3100}}), at(2), state)
    assert "ESCALATING" in text and "3.1k" in text and "90" in text


def test_jitter_within_a_band_is_not_an_escalation():
    _, state = TRIAGE.build_report(rows({OTLP: {"open-webui": 400}}), at(1), None)
    text, _ = TRIAGE.build_report(rows({OTLP: {"open-webui": 900}}), at(2), state)
    assert "ESCALATING" not in text, "same order of magnitude is normal jitter"


def test_a_signature_that_moves_hosts_keeps_its_identity():
    """Identity is the fault, not the machine — a fault spreading to a second
    host is the SAME signature with a wider blast radius, not a new one."""
    _, state = TRIAGE.build_report(rows({TMPMOUNT: {"a": 400}}), at(1), None)
    text, _ = TRIAGE.build_report(rows({TMPMOUNT: {"a": 400, "b": 60}}), at(2), state)
    assert "*NEW*" not in text, "the same fault on one more host is not a new fault"


def test_zero_rows_is_stated_not_smoothed_into_all_clear():
    text, state = TRIAGE.build_report([], at(1), None)
    assert "Zero rows" in text and "ingest has stopped" in text
    assert "no errors" not in text.lower()
    text, _ = TRIAGE.build_report([], at(2), state)
    assert text == TRIAGE.SILENT, "routine zero-rows is once per day too"


def test_findings_over_the_cap_are_not_ledgered():
    """A novel finding that did not fit this run must surface on the next one."""
    many = rows({f"sig-{i:02d} failed to do the thing": {"h": 500 - i}
                 for i in range(TRIAGE.MAX_FINDINGS + 3)})
    _, state = TRIAGE.build_report(many, at(1), None)
    text, _ = TRIAGE.build_report(many, at(2), state)
    dropped = [f"sig-{i:02d}" for i in range(TRIAGE.MAX_FINDINGS, TRIAGE.MAX_FINDINGS + 3)]
    assert any(s in text for s in dropped), \
        "a finding cut by the output cap was ledgered as if it had been posted"


def test_the_scope_line_admits_what_the_top_n_cap_hid():
    text, _ = TRIAGE.build_report(
        rows({RAFT: {"openbao-02": 400}}, total_sigs=47), at(1), None)
    assert "top 1 of 47 error signatures" in text, \
        "printing only the count it kept would imply it saw them all"


def test_unusable_rows_are_dropped_not_guessed():
    noisy = rows({RAFT: {"openbao-02": 400}}) + [
        {"sig": "", "host": "x", "sourcetype": "y", "count": "5"},
        {"sig": "z", "host": "x", "sourcetype": "y", "count": "not-a-number"},
        {"sig": "w", "host": "z", "sourcetype": "w", "count": "0"},
    ]
    _, state = TRIAGE.build_report(noisy, at(1), None)
    assert state["counts"] == {TRIAGE.sig_key(RAFT): 400}


def test_a_row_without_host_is_reported_not_silently_dropped():
    """Observed live: `stats count by ... host` omits `host` entirely when it is
    unset, and one such row carried 44727 events. Dropping it would print a
    confident total that understates real error volume."""
    real = [{"sig": TMPMOUNT, "sourcetype": "syslog", "count": "44727"}]
    text, state = TRIAGE.build_report(real, at(1), None)
    assert state["hosts"] == {TRIAGE.NO_HOST: 44727}
    assert "44.7k" in text and TRIAGE.NO_HOST in text
    assert "Zero rows" not in text, "real data must never be reported as zero rows"


def test_an_older_state_schema_is_treated_as_no_baseline():
    stale = {"schema": TRIAGE.STATE_SCHEMA - 1, "counts": {TRIAGE.sig_key(RAFT): 5}}
    text, _ = TRIAGE.build_report(rows({RAFT: {"openbao-02": 400}}), at(1), stale)
    assert "ESCALATING" not in text and "NEW" not in text


def test_delivered_text_never_contains_tool_call_markup():
    """Belt and braces: the script's own output must pass the delivery guard."""
    text, _ = TRIAGE.build_report(rows({RAFT: {"openbao-02": 400}}), at(1), None)
    assert GUARD(JOB, "out.md", text) == text


# --- the window must be applied where Splunk actually honours it --------------


def test_the_window_is_written_inline_in_the_spl():
    """PROVEN LIVE 2026-07-28: `splunk_run_query`'s `earliest` ARGUMENT IS
    IGNORED — the same query returned byte-identical results for -1h, -24h and
    -7d. Every hourly digest was therefore a ~24h figure under a "last 1h"
    heading, which is how one host read as 18k errors/hour when its true hourly
    rate was 36. The window must be in the SPL, where Splunk applies it."""
    assert f"earliest={TRIAGE.EARLIEST} latest=now" in TRIAGE.SPL, \
        "the search string itself must carry the window"
    assert TRIAGE.SPL.index("earliest=") < TRIAGE.SPL.index("| eval sig="), \
        "the bound belongs in the base search, before the transforming commands"


def test_the_signature_normalisation_cannot_be_reordered_into_uselessness():
    """The catch-all digit rule must come AFTER the timestamp rule. Reversed, an
    ISO timestamp becomes <n>-<n>-<n>T<n>:<n>:<n> and one fault splits into as
    many signatures as it has distinct timestamps — the exact failure the whole
    change exists to remove."""
    rules = TRIAGE.SED_RULES
    catch_all = next(i for i, r in enumerate(rules) if r == r"s/\d+/<n>/g")
    # Substring, not regex: these fragments ARE regex source and must be
    # compared literally against the rule text.
    for fragment, what in ((r"<ts>", "timestamp"), (r"<pid>", "pid"),
                           (r"<ip>", "IP address"), (r"<hex>", "hex id")):
        matches = [i for i, r in enumerate(rules) if fragment in r]
        assert matches, f"no rule normalises the {what}"
        assert max(matches) < catch_all, \
            f"the {what} rule must precede the catch-all digit rule"
    assert rules[0].startswith("s/^<"), "the syslog PRI strip is anchored and goes first"
    assert catch_all == len(rules) - 2, \
        "only whitespace collapse may follow the catch-all digit rule"


def test_a_clipped_signature_says_it_was_clipped():
    """An unmarked cut reads as the whole message and hides where two faults
    that share a prefix actually differ."""
    long_sig = "x" * (TRIAGE.SIG_CHARS + 40)
    text, _ = TRIAGE.build_report(rows({long_sig: {"a": 30}}), at(1), None)
    assert "…" in text
    assert TRIAGE.clip_sig("y" * TRIAGE.SIG_CHARS) == "y" * TRIAGE.SIG_CHARS, \
        "an exactly-full-width signature must NOT be marked as truncated"
    assert f"substr(sig,1,{TRIAGE.SIG_CHARS + 1})" in TRIAGE.SPL, (
        "Splunk must return one char more than the report shows, or the "
        "formatter cannot tell a clipped signature from a full-width one")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"ok  {name}")
    print(f"\n{passed} checks passed")
