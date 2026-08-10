"""Self-check for the Splunk triage escalation ladder (tier 2 hosts, tier 3 mix).

Split from test_splunk_triage.py to stay under the token budget — see
_splunk_triage_shared.py for the loaded template/guard fixtures and
test_splunk_triage_novelty.py for the per-day novelty contract this leaves
behind.

Tier 1 (test_splunk_triage_novelty.py) buckets by order of magnitude; the
ladder buckets by percent. That is what makes them complementary: a +60%
move keeps the same OOM bucket, so tier 1 has nothing new to say about it
while tier 2 does.

Runs bare (`python3 tests/hermes_agent/test_splunk_triage_ladder.py`) or
under pytest. Plain asserts, no fixtures, no framework.
"""
from _splunk_triage_shared import GUARD, JOB, OTLP, TMPMOUNT, TRIAGE, at, mixed, rows


def test_ladder_fires_only_when_tier_one_is_exhausted():
    first = rows({TMPMOUNT: {"a": 100}})
    text, state = TRIAGE.build_report(first, at(1), None)
    assert "tmp.mount" in text
    text, _ = TRIAGE.build_report(rows({TMPMOUNT: {"a": 160}}), at(2), state)
    assert "Host a up +60%" in text, "a percent move tier 1 cannot see must escalate"
    assert "No new signatures" not in text


def test_tier_one_content_suppresses_the_ladder():
    _, state = TRIAGE.build_report(rows({TMPMOUNT: {"a": 100}}), at(1), None)
    text, _ = TRIAGE.build_report(
        rows({TMPMOUNT: {"a": 100}, OTLP: {"b": 90}}), at(2), state)
    assert "*NEW*" in text
    assert "Host " not in text, "the ladder is a fallback, not an addition"


def test_a_moderate_climb_is_routine_and_does_not_repeat():
    _, state = TRIAGE.build_report(rows({TMPMOUNT: {"a": 100}}), at(1), None)
    later = rows({TMPMOUNT: {"a": 160}})
    text, state = TRIAGE.build_report(later, at(2), state)
    assert "Host a up +60%" in text
    text, _ = TRIAGE.build_report(later, at(3), state)
    assert "Host a" not in text, "a band-edge wobble must not repeat all day"


def test_a_climb_is_reported_once_because_the_baseline_catches_up():
    """Ladder findings are deltas against the PREVIOUS run, so no ladder finding
    can persist: once the baseline advances, the move is gone. That is why none
    of them are marked critical — a critical delta could not repeat a real
    escalation, only a band-edge oscillation."""
    _, state = TRIAGE.build_report(rows({TMPMOUNT: {"a": 100}}), at(1), None)
    later = rows({TMPMOUNT: {"a": 500}})
    text, state = TRIAGE.build_report(later, at(2), state)
    assert "Host a up +400%" in text
    text, _ = TRIAGE.build_report(later, at(3), state)
    assert "Host a" not in text, "500 -> 500 is not a move"


def test_an_oscillating_host_does_not_re_post_the_same_move_all_day():
    quiet, loud = rows({TMPMOUNT: {"a": 100}}), rows({TMPMOUNT: {"a": 500}})
    _, state = TRIAGE.build_report(quiet, at(1), None)
    text, state = TRIAGE.build_report(loud, at(2), state)
    assert "Host a up +400%" in text
    _, state = TRIAGE.build_report(quiet, at(3), state)
    text, _ = TRIAGE.build_report(loud, at(4), state)
    assert "Host a up +400%" not in text, "the same swing must not re-post today"


def test_a_host_that_stopped_reporting_is_surfaced():
    """Tier 1 only walks CURRENT signatures, so a host disappearing entirely is
    information it structurally cannot produce."""
    _, state = TRIAGE.build_report(
        rows({TMPMOUNT: {"a": 100, "b": 100}}), at(1), None)
    text, _ = TRIAGE.build_report(rows({TMPMOUNT: {"a": 100}}), at(2), state)
    assert "Stopped reporting: b" in text


def test_a_share_shift_is_critical_even_when_volume_is_flat():
    """The mix changing is how a new failure mode announces itself while totals
    stay put — the thing a volume-only view misses."""
    before = TRIAGE.rollup(
        TRIAGE.parse_rows(mixed({TMPMOUNT: {"a": {"syslog": 100, "audit": 100}}}))[0],
        "sourcetypes")
    after = TRIAGE.rollup(
        TRIAGE.parse_rows(mixed({TMPMOUNT: {"a": {"syslog": 40, "audit": 160}}}))[0],
        "sourcetypes")
    shares = [f for f in TRIAGE.composition_findings(after, before, True)
              if f.key.startswith("share:")]
    assert shares, "a 30-point mix shift must be a finding"
    assert all(f.critical for f in shares)


def test_a_move_too_small_to_mean_anything_is_not_a_finding():
    assert TRIAGE.move_of(11, 10) is None, "series under MIN_MOVER_COUNT"
    assert TRIAGE.move_of(105, 100) is None, "swing under the smallest band"
    assert TRIAGE.move_of(100, None) is None, "no baseline"


def test_ladder_findings_never_contain_tool_call_markup():
    _, state = TRIAGE.build_report(rows({TMPMOUNT: {"a": 100}}), at(1), None)
    text, _ = TRIAGE.build_report(rows({TMPMOUNT: {"a": 500}}), at(2), state)
    assert GUARD(JOB, "out.md", text) == text


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"ok  {name}")
    print(f"\n{passed} checks passed")
