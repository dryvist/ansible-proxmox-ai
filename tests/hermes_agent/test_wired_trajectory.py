"""Self-check for the wired-memory-ceiling trajectory watch script.

Renders the deployed splunk-wired-trajectory.py.j2 (same technique as
_splunk_triage_shared.py: substitute the Jinja config lines, exec as a module)
so this exercises the shipped artifact, not a hand-copied approximation.

Runs bare (`python3 tests/hermes_agent/test_wired_trajectory.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
import re
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/splunk-wired-trajectory.py.j2"

FIXTURE_CONFIG = {
    "ENV_PATH": "/tmp/unused.env",
    "TITLE": "Wired-memory trajectory [wired-trajectory]",
    "INDEX": "mac_perf",
    "SOURCETYPE": "macos-wired-memory",
    "EARLIEST": "-6h",
    "ISSUES_MARKER": "[ISSUES]",
    "STATE_PATH": "/tmp/wired-trajectory-selfcheck/state.json",
}
FIXTURE_RATIO = {"CRITICAL_RATIO": 0.90, "HORIZON_HOURS": 6}


def load_module():
    out = []
    for line in TEMPLATE_PATH.read_text().splitlines():
        if "ansible_managed" in line:
            continue
        match = re.match(r"^(\w+) = .*\{\{", line)
        if match:
            name = match.group(1)
            if name in FIXTURE_CONFIG:
                out.append(f"{name} = {FIXTURE_CONFIG[name]!r}")
            elif name in FIXTURE_RATIO:
                out.append(f"{name} = {FIXTURE_RATIO[name]!r}")
            else:
                raise AssertionError(f"template config {name} has no self-check fixture")
            continue
        out.append(line)
    rendered = "\n".join(out)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("splunk_wired_trajectory")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


MOD = load_module()

HOUR = 3600


def samples(ratios, spacing=15 * 60, start=0):
    """[(epoch, ratio, wired_bytes, ceiling_bytes), ...] evenly spaced."""
    return [(start + i * spacing, r, r * 100e9, 100e9) for i, r in enumerate(ratios)]


def test_zero_rows_is_stated_not_silent():
    report, _ = MOD.build_report([])
    assert "Zero rows" in report
    assert "mac_perf" in report


def test_step_change_beats_slow_climb_when_both_present():
    # A rank load: one big jump (0.10 -> 0.30 within one 15-min sample,
    # implying 0.8 ratio/hr) then flat — must read as STEP, not CLIMB.
    s = samples([0.10, 0.10, 0.10, 0.30, 0.30, 0.30])
    report, _ = MOD.build_report(s)
    assert "STEP CHANGE" in report
    assert "SLOW CLIMB" not in report


def test_slow_sustained_climb_reads_as_leak_with_an_eta():
    # Steady 15-min-cadence climb: 0.10 -> 0.40 over 6h = 0.05/hr, each
    # sample-to-sample step only 0.0125 — nowhere near step-change territory.
    ratios = [0.10 + 0.0125 * i for i in range(25)]
    s = samples(ratios, spacing=15 * 60)
    report, _ = MOD.build_report(s)
    assert "SLOW CLIMB" in report
    assert "STEP CHANGE" not in report
    assert "leak" in report.lower()
    assert "h to the 0.90" in report


def test_flat_series_is_stable_not_climbing():
    s = samples([0.10] * 6, spacing=15 * 60)
    report, _ = MOD.build_report(s)
    assert "Stable" in report
    assert "STEP CHANGE" not in report and "SLOW CLIMB" not in report


def test_falling_series_reported_as_falling():
    ratios = [0.40 - 0.0125 * i for i in range(25)]
    s = samples(ratios, spacing=15 * 60)
    report, _ = MOD.build_report(s)
    assert "Falling" in report


def test_slope_per_hour_matches_known_linear_series():
    s = samples([0.10, 0.15, 0.20, 0.25], spacing=HOUR)
    slope = MOD.slope_per_hour(s)
    assert abs(slope - 0.05) < 1e-9


def test_single_sample_has_no_slope_and_no_step():
    s = samples([0.5])
    assert MOD.slope_per_hour(s) is None
    assert MOD.max_step_rate(s) == 0.0


def test_step_rate_is_cadence_normalized():
    # Same 0.10 delta: a 15-min gap implies a fast rate (real step), a 6h gap
    # implies a slow one (well within ordinary climb territory) — the
    # detector must not treat a raw delta the same regardless of spacing.
    fast = samples([0.10, 0.30], spacing=15 * 60)
    slow = samples([0.10, 0.30], spacing=6 * HOUR)
    assert MOD.max_step_rate(fast) > MOD.STEP_IMPLIED_RATE_PER_HOUR
    assert MOD.max_step_rate(slow) < MOD.STEP_IMPLIED_RATE_PER_HOUR


# --- verdict gate (Vikunja 1859): post only a climb inside the horizon -------


def test_verdicts_are_named_for_every_shape():
    _, v = MOD.build_report([])
    assert v == "NO_DATA"
    _, v = MOD.build_report(samples([0.10, 0.10, 0.10, 0.30, 0.30, 0.30]))
    assert v == "STEP"
    # 0.10 -> 0.40 over 6h at 0.05/hr: 10h from 0.90, outside a 6h horizon.
    _, v = MOD.build_report(samples([0.10 + 0.0125 * i for i in range(25)]))
    assert v == "CLIMB_DISTANT"
    _, v = MOD.build_report(samples([0.30] * 6))
    assert v == "STABLE"
    _, v = MOD.build_report(samples([0.50 - 0.02 * i for i in range(6)]))
    assert v == "FALLING"


def test_a_climb_is_only_a_climb_inside_the_horizon():
    # 0.70 -> 0.85 over 6h = 0.025/hr: 2h from 0.90 -> inside a 6h horizon.
    _, near = MOD.build_report(samples([0.70 + 0.00625 * i for i in range(25)]))
    assert near == "CLIMB"
    # 0.10 -> 0.25 over 6h = 0.025/hr: 26h from 0.90 -> a trend, not a warning.
    _, far = MOD.build_report(samples([0.10 + 0.00625 * i for i in range(25)]))
    assert far == "CLIMB_DISTANT"


def test_only_a_near_climb_or_a_broken_path_posts_and_all_clears_post_once():
    assert MOD.should_post("CLIMB", None) == (True, False)
    assert MOD.should_post("CLIMB", "CLIMB") == (True, False), "an approach warning repeats while it holds"
    assert MOD.should_post("NO_DATA", None) == (True, False)
    for quiet in ("STEP", "FALLING", "STABLE", "CLIMB_DISTANT"):
        assert MOD.should_post(quiet, None) == (False, False), quiet
        assert MOD.should_post(quiet, "STABLE") == (False, False), quiet
        assert MOD.should_post(quiet, "CLIMB") == (True, True), f"{quiet} after a climb is the all-clear"


def test_render_routes_no_data_to_issues_once_and_stays_silent_after():
    text, v = MOD.build_report([])
    first = MOD.render(text, v, None)
    assert first.startswith("[ISSUES]") and "Zero rows" in first
    assert MOD.render(text, v, "NO_DATA") == "[SILENT]"
    # Rows came back: one all-clear, then silence.
    stable_text, stable = MOD.build_report(samples([0.30] * 6))
    again = MOD.render(stable_text, stable, "NO_DATA")
    assert again.startswith(":white_check_mark:") and "samples flowing again" in again
    assert MOD.render(stable_text, stable, "STABLE") == "[SILENT]"


def test_verdict_state_round_trips_through_the_state_file():
    import pathlib
    import tempfile

    state_dir = pathlib.Path(tempfile.mkdtemp(prefix="wired-trajectory-selfcheck-"))
    MOD.__dict__["STATE_PATH"] = str(state_dir / "state.json")
    assert MOD.load_previous_verdict() is None
    MOD.save_verdict("CLIMB")
    assert MOD.load_previous_verdict() == "CLIMB"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL: {name}: {e}")
    print(f"\n{'All tests passed.' if not failures else f'{failures} failure(s).'}")
    raise SystemExit(1 if failures else 0)
