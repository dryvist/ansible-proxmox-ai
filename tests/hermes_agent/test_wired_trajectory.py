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
}
FIXTURE_RATIO = {"CRITICAL_RATIO": 0.90}


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
    report = MOD.build_report([])
    assert "Zero rows" in report
    assert "mac_perf" in report


def test_step_change_beats_slow_climb_when_both_present():
    # A rank load: one big jump (0.10 -> 0.30 within one 15-min sample,
    # implying 0.8 ratio/hr) then flat — must read as STEP, not CLIMB.
    s = samples([0.10, 0.10, 0.10, 0.30, 0.30, 0.30])
    report = MOD.build_report(s)
    assert "STEP CHANGE" in report
    assert "SLOW CLIMB" not in report


def test_slow_sustained_climb_reads_as_leak_with_an_eta():
    # Steady 15-min-cadence climb: 0.10 -> 0.40 over 6h = 0.05/hr, each
    # sample-to-sample step only 0.0125 — nowhere near step-change territory.
    ratios = [0.10 + 0.0125 * i for i in range(25)]
    s = samples(ratios, spacing=15 * 60)
    report = MOD.build_report(s)
    assert "SLOW CLIMB" in report
    assert "STEP CHANGE" not in report
    assert "leak" in report.lower()
    assert "h to the 0.90" in report


def test_flat_series_is_stable_not_climbing():
    s = samples([0.10] * 6, spacing=15 * 60)
    report = MOD.build_report(s)
    assert "Stable" in report
    assert "STEP CHANGE" not in report and "SLOW CLIMB" not in report


def test_falling_series_reported_as_falling():
    ratios = [0.40 - 0.0125 * i for i in range(25)]
    s = samples(ratios, spacing=15 * 60)
    report = MOD.build_report(s)
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
