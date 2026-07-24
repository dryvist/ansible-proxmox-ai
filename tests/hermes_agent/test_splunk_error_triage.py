"""Self-check for the Splunk error-triage per-day novelty gate.

The script is a Jinja template whose STDOUT goes verbatim to Slack, so the contract
under test is the *emitted text*: routine findings appear once per UTC day, critical
conditions repeat every run, a run with nothing novel escalates instead of emitting
boilerplate, and findings beyond the per-run cap stay unledgered.

Runs bare (`python3 tests/hermes_agent/test_splunk_error_triage.py`) or under pytest.
Plain asserts, no fixtures, no framework.
"""
import datetime as dt
import re
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/splunk-error-triage.py.j2"
TEMPLATE = TEMPLATE_PATH.read_text()

STATE_DIR = tempfile.mkdtemp(prefix="splunk-error-triage-selfcheck-")
# Stand-ins for the values Ansible renders from roles/hermes_agent/defaults/main.yml.
FIXTURE_CONFIG = {
    "ENV_PATH": str(Path(STATE_DIR) / "unused.env"),
    "STATE_PATH": str(Path(STATE_DIR) / "splunk-error-triage.json"),
    "EARLIEST": "-1h",
    "QUERY": "search (index=os OR index=network) (error OR failed OR critical) "
             "| stats count by host, sourcetype",
    "CRIT_SOURCE_COUNT": 5000,
}

DAY1 = dt.datetime(2026, 7, 24, 13, 37, tzinfo=dt.timezone.utc)
DAY1_LATER = dt.datetime(2026, 7, 24, 14, 37, tzinfo=dt.timezone.utc)
DAY2 = dt.datetime(2026, 7, 25, 0, 37, tzinfo=dt.timezone.utc)


def load_module():
    """Render the template's config lines to fixtures and import it as a module."""
    out, pending = [], None
    for line in TEMPLATE.splitlines():
        if "ansible_managed" in line:
            continue
        # QUERY spans several lines; swallow the continuation once it starts.
        if pending:
            if line.rstrip().endswith(")"):
                pending = None
            continue
        match = re.match(r"^(\w+) = .*\{\{", line) or re.match(r'^(\w+) = \("', line)
        if match:
            name = match.group(1)
            assert name in FIXTURE_CONFIG, f"template config {name} has no self-check fixture"
            out.append(f"{name} = {FIXTURE_CONFIG[name]!r}")
            if not line.rstrip().endswith(")") and line.lstrip().startswith(f"{name} = ("):
                pending = name
            continue
        out.append(line)
    rendered = "\n".join(out)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("splunk_error_triage")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


TRIAGE = load_module()


def rows(spec):
    """Build stats rows from {host: {sourcetype: count}} — values as Splunk returns them."""
    return [
        {"host": host, "sourcetype": stype, "count": str(count)}
        for host, types_ in spec.items()
        for stype, count in types_.items()
    ]


BASE = {
    "host-a": {"syslog": 400, "auth": 120},
    "host-b": {"syslog": 250},
}


def run(results, now, state):
    return TRIAGE.build_triage(results, now, state)


def test_first_run_of_day_posts_the_table_and_says_it_is_the_baseline():
    text, state = run(rows(BASE), DAY1, None)
    assert "First triage of 2026-07-24" in text
    assert "host-a / syslog" in text and "400" in text
    assert "no baseline" in text, "delta cells must say why a delta is missing"
    assert state["ledger"]["day"] == "2026-07-24"
    assert any(k.startswith("table:") for k in state["ledger"]["keys"])


def test_identical_rerun_same_day_does_not_repeat_the_table():
    _, state = run(rows(BASE), DAY1, None)
    text, _ = run(rows(BASE), DAY1_LATER, state)
    assert "First triage" not in text, "routine info must be presented once per day"
    assert "Nothing new to report" in text
    assert "host/sourcetype source(s)" in text, "the exhausted line must name what it covered"


def test_new_day_resets_the_ledger():
    _, state = run(rows(BASE), DAY1, None)
    text, fresh = run(rows(BASE), DAY2, state)
    assert "First triage of 2026-07-25" in text, "a new UTC day re-presents the baseline table"
    assert fresh["ledger"]["day"] == "2026-07-25"


def test_critical_repeats_every_run_ignoring_the_ledger():
    hot = {"host-a": {"syslog": 9000}}
    _, state = run(rows(hot), DAY1, None)
    text, state2 = run(rows(hot), DAY1_LATER, state)
    assert "Critical" in text and "9,000" in text, "a persisting storm must not go quiet"
    text3, _ = run(rows(hot), DAY1_LATER, state2)
    assert "Critical" in text3


def test_nothing_novel_at_source_level_escalates_rather_than_saying_nothing():
    # A new host erroring just under MIN_MOVER_COUNT: too small to be a source
    # finding, but it still changes the erroring-host set, so only tier 2 has
    # anything unsaid.
    _, state = run(rows(BASE), DAY1, None)
    joined = dict(BASE, **{"host-c": {"syslog": TRIAGE.MIN_MOVER_COUNT - 5}})
    text, _ = run(rows(joined), DAY1_LATER, state)
    assert "Newly erroring: host-c" in text
    assert "Nothing new to report" not in text
    assert "nothing new at" in text, "an escalated run must name the tiers it exhausted"


def test_findings_beyond_the_cap_are_not_ledgered():
    many = {f"host-{n}": {"syslog": 100} for n in range(TRIAGE.MAX_FINDINGS + 6)}
    _, state = run(rows(many), DAY1, None)
    # Every source is new, so the tier overflows; only what printed may be ledgered.
    assert len(state["ledger"]["keys"]) <= TRIAGE.MAX_FINDINGS, \
        "unshown novel findings must stay novel for a later run"


def test_zero_rows_is_stated_not_silent():
    text, _ = run([], DAY1, None)
    assert "zero rows" in text, "an empty result must be an explicit fact, never silence"


def test_small_series_never_produce_percent_findings():
    _, state = run(rows({"host-a": {"syslog": 3}}), DAY1, None)
    text, _ = run(rows({"host-a": {"syslog": 9}}), DAY1_LATER, state)
    assert "%" not in text, "3 -> 9 events is +200% and means nothing"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all error-triage self-checks passed")
