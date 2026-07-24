"""Self-check for the script-fed Splunk error triage and the cron markup guard.

Two contracts, both enforced here:

1. Never deliver tool-call markup — the guard patched into cron/scheduler.py
   replaces any `<function=...>` / `<parameter=...>` / `<tool_call>` response
   with a diagnostic naming the job. The guard body lives in a blockinfile in
   roles/hermes_agent/tasks/main.yml, so it is extracted from there and executed:
   the thing under test is the code that actually ships.
2. Per-day novelty — a steady error stream is presented ONCE per UTC day; NEW
   and ESCALATING streams are critical and repeat every run while they hold.
   Never fabricate: zero rows and an absent baseline are stated as themselves.

Runs bare (`python3 tests/hermes_agent/test_splunk_error_triage.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
import datetime as dt
import logging
import re
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/splunk-error-triage.py.j2"
TASKS_PATH = REPO_ROOT / "roles/hermes_agent/tasks/main.yml"

STATE_DIR = tempfile.mkdtemp(prefix="splunk-error-triage-selfcheck-")
# Stand-ins for the values Ansible renders from roles/hermes_agent/defaults/main.yml.
FIXTURE_CONFIG = {
    "ENV_PATH": str(Path(STATE_DIR) / "unused.env"),
    "STATE_PATH": str(Path(STATE_DIR) / "splunk-error-triage.json"),
    "INDEXES": ["os", "network"],
    "TERMS": ["error", "failed", "critical"],
    "EARLIEST": "-1h",
    "TOP_N": 12,
    "MAX_FINDINGS": 8,
}


def load_triage_module():
    """Render the template's config lines to fixtures and import it as a module."""
    out = []
    for line in TEMPLATE_PATH.read_text().splitlines():
        if "ansible_managed" in line:
            continue
        match = re.match(r"^(\w+) = .*\{\{", line)
        if match:
            name = match.group(1)
            assert name in FIXTURE_CONFIG, f"template config {name} has no self-check fixture"
            out.append(f"{name} = {FIXTURE_CONFIG[name]!r}")
            continue
        out.append(line)
    rendered = "\n".join(out)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("splunk_error_triage")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


def load_markup_guard():
    """Extract the guard from its blockinfile in tasks/main.yml and import it.

    The block is indented under `block: |` in YAML; dedent it back to module
    level. Executing the shipped text (rather than a copy) is the point — a
    drifted guard fails here instead of in Slack.
    """
    lines = TASKS_PATH.read_text().splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.strip() == "block: |"
                 and "def _cron_markup_guard" in "\n".join(lines[i:i + 3]))
    body = []
    indent = None
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith(" " * 6):
            break
        if indent is None and line.strip():
            indent = len(line) - len(line.lstrip())
        body.append(line[indent:] if line.strip() else "")
    source = "\n".join(body)
    assert "def _cron_markup_guard" in source, "guard block not found in tasks/main.yml"
    mod = types.ModuleType("cron_markup_guard")
    mod.re = re
    mod.logger = logging.getLogger("selfcheck")
    exec(compile(source, str(TASKS_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod._cron_markup_guard


TRIAGE = load_triage_module()
GUARD = load_markup_guard()
JOB = {"id": "cc8872cfb71f", "name": "splunk-error-triage"}
DAY = dt.datetime(2026, 7, 24, 19, 38, tzinfo=dt.timezone.utc)


def at(hour, day=24):
    return DAY.replace(day=day, hour=hour)


def rows(spec):
    """Build stats rows from {host: {sourcetype: count}}."""
    return [{"host": h, "sourcetype": st, "count": str(c)}
            for h, sts in spec.items() for st, c in sts.items()]


# --- contract 1: tool-call markup never reaches delivery ---------------------

def test_guard_suppresses_the_observed_leak():
    """The exact payload that reached Slack, verbatim from the stored run."""
    leak = (
        "<function=mcp__splunk__splunk_run_query>\n"
        "<parameter=query>\n"
        "search index=os OR index=network (error OR failed OR critical) earliest=-1h\n"
        "</parameter>"
    )
    out = GUARD(JOB, "/var/lib/hermes/out.md", leak)
    assert "<function=" not in out and "<parameter=" not in out
    assert "splunk-error-triage" in out, "diagnostic must name the job"
    assert "/var/lib/hermes/out.md" in out, "diagnostic must point at the raw run"


def test_guard_catches_native_tool_call_format_too():
    assert "<tool_call>" not in GUARD(JOB, "out.md", "<tool_call>{}</tool_call>")


def test_guard_passes_a_real_report_through_untouched():
    report = "*Splunk error triage*\npve3 / syslog — 412 events.\nScope: 2 streams."
    assert GUARD(JOB, "out.md", report) == report


def test_guard_survives_empty_and_none():
    assert GUARD(JOB, "out.md", "") == ""
    assert GUARD(JOB, "out.md", None) is None


def test_guard_names_the_job_by_id_when_unnamed():
    assert "cc8872cfb71f" in GUARD({"id": "cc8872cfb71f"}, "out.md", "<function=x>")


# --- contract 2: per-day novelty, and never fabricate ------------------------

def test_first_run_reports_counts_and_claims_no_change():
    text, state = TRIAGE.build_report(rows({"pve3": {"syslog": 400}}), at(1), None)
    assert "pve3 / syslog" in text and "400" in text
    assert "NEW" not in text and "ESCALATING" not in text, \
        "nothing can be new against an absent baseline"
    assert state["counts"] == {"pve3|syslog": 400}


def test_steady_stream_is_presented_once_per_day():
    data = rows({"pve3": {"syslog": 400}})
    text, state = TRIAGE.build_report(data, at(1), None)
    assert "pve3 / syslog" in text, "the day's first run presents the stream"
    text, state = TRIAGE.build_report(data, at(2), state)
    assert "pve3 / syslog" not in text, "a steady stream must not repeat within the day"
    assert "No new error signatures" in text, "the day gets one exhausted-search line"
    assert str(len(TRIAGE.parse_rows(data))) in text, "and it names the space it covered"
    text, _ = TRIAGE.build_report(data, at(3), state)
    assert text == TRIAGE.SILENT, "after that, silence for the rest of the day"


def test_a_new_utc_day_resets_the_ledger():
    data = rows({"pve3": {"syslog": 400}})
    _, state = TRIAGE.build_report(data, at(1), None)
    _, state = TRIAGE.build_report(data, at(2), state)
    text, state = TRIAGE.build_report(data, at(3), state)
    assert text == TRIAGE.SILENT
    text, _ = TRIAGE.build_report(data, at(1, day=25), state)
    assert "pve3 / syslog" in text, "a new UTC day re-presents routine information"


def test_a_new_error_source_is_critical_and_repeats():
    _, state = TRIAGE.build_report(rows({"pve3": {"syslog": 400}}), at(1), None)
    both = rows({"pve3": {"syslog": 400}, "fw01": {"cisco:asa": 90}})
    text, state = TRIAGE.build_report(both, at(2), state)
    assert "NEW error source fw01 / cisco:asa" in text
    text, state = TRIAGE.build_report(both, at(3), state)
    assert "fw01 / cisco:asa" in text, "critical findings ignore the ledger"


def test_an_order_of_magnitude_climb_is_escalating():
    _, state = TRIAGE.build_report(rows({"fw01": {"cisco:asa": 90}}), at(1), None)
    text, _ = TRIAGE.build_report(rows({"fw01": {"cisco:asa": 3100}}), at(2), state)
    assert "ESCALATING" in text and "3.1k" in text and "90" in text


def test_jitter_within_a_band_is_not_an_escalation():
    _, state = TRIAGE.build_report(rows({"fw01": {"cisco:asa": 400}}), at(1), None)
    text, _ = TRIAGE.build_report(rows({"fw01": {"cisco:asa": 900}}), at(2), state)
    assert "ESCALATING" not in text, "same order of magnitude is normal jitter"


def test_zero_rows_is_stated_not_smoothed_into_all_clear():
    text, state = TRIAGE.build_report([], at(1), None)
    assert "Zero rows" in text and "ingest has stopped" in text
    assert "no errors" not in text.lower()
    text, _ = TRIAGE.build_report([], at(2), state)
    assert text == TRIAGE.SILENT, "routine zero-rows is once per day too"


def test_findings_over_the_cap_are_not_ledgered():
    """A novel finding that did not fit this run must surface on the next one."""
    many = rows({f"host{i:02d}": {"syslog": 500 - i} for i in range(TRIAGE.MAX_FINDINGS + 3)})
    _, state = TRIAGE.build_report(many, at(1), None)
    text, _ = TRIAGE.build_report(many, at(2), state)
    dropped = [f"host{i:02d}" for i in range(TRIAGE.MAX_FINDINGS, TRIAGE.MAX_FINDINGS + 3)]
    assert any(h in text for h in dropped), \
        "a finding cut by the output cap was ledgered as if it had been posted"


def test_unusable_rows_are_dropped_not_guessed():
    noisy = rows({"pve3": {"syslog": 400}}) + [
        {"host": "x", "sourcetype": "", "count": "5"},
        {"host": "x", "sourcetype": "y", "count": "not-a-number"},
        {"host": "z", "sourcetype": "w", "count": "0"},
    ]
    _, state = TRIAGE.build_report(noisy, at(1), None)
    assert state["counts"] == {"pve3|syslog": 400}


def test_a_row_without_host_is_reported_not_silently_dropped():
    """Observed live: `stats count by host, sourcetype` omits `host` entirely
    when it is unset, and that row carried 44727 events. Dropping it would print
    a confident total that understates real error volume."""
    real = [{"sourcetype": "syslog", "count": "44727"}]
    text, state = TRIAGE.build_report(real, at(1), None)
    assert state["counts"] == {f"{TRIAGE.NO_HOST}|syslog": 44727}
    assert "44.7k" in text and TRIAGE.NO_HOST in text
    assert "Zero rows" not in text, "real data must never be reported as zero rows"


def test_an_older_state_schema_is_treated_as_no_baseline():
    stale = {"schema": TRIAGE.STATE_SCHEMA - 1, "counts": {"pve3|syslog": 5}}
    text, _ = TRIAGE.build_report(rows({"pve3": {"syslog": 400}}), at(1), stale)
    assert "ESCALATING" not in text and "NEW" not in text


def test_delivered_text_never_contains_tool_call_markup():
    """Belt and braces: the script's own output must pass the delivery guard."""
    text, _ = TRIAGE.build_report(rows({"pve3": {"syslog": 400}}), at(1), None)
    assert GUARD(JOB, "out.md", text) == text


# --- no cron name may be a substring of another ------------------------------

def test_no_reconciled_cron_name_is_a_substring_of_another_job():
    """Both reconcilers test job existence with `name in cron_list_stdout`.

    A RECONCILED name contained in some other job's name therefore reads as
    already-present, and the drift branch fires `cron remove` for a job that
    does not exist — failing the converge on a fresh guest.

    Caught for real: naming the script job `splunk-error-triage` put it inside
    the paused `splunk-error-triage-v2`, which `cron list --all` still prints.

    Only reconciled names are checked against the full universe of names that
    can appear in that listing. The kanban `job:` values are not themselves cron
    names (the reconciler appends `-enqueue`), and the superseded-removal list
    matches with exact membership, not substring — neither is a hazard here.
    """
    import itertools
    import yaml

    defaults = yaml.safe_load((REPO_ROOT / "roles/hermes_agent/defaults/main.yml").read_text())
    direct = {job["name"] for job in defaults["hermes_agent_direct_cron_jobs"]}
    enqueuers = {card["job"] + "-enqueue" for card in defaults.get("hermes_agent_kanban_cards", [])}
    scripts = {
        defaults["hermes_agent_error_triage_cron_name"],
        defaults["hermes_agent_splunk_status_digest_cron_name"],
        defaults["hermes_agent_kanban_safety_net_cron_name"],
    }
    reconciled = direct | enqueuers | scripts
    # Everything `cron list --all` can print, including paused/superseded jobs.
    universe = reconciled | {v for k, v in defaults.items()
                             if k.endswith("_cron_name") and isinstance(v, str)}
    collisions = [(r, n) for r, n in itertools.product(sorted(reconciled), sorted(universe))
                  if r != n and r in n]
    assert not collisions, f"reconciled cron name(s) contained in another job's name: {collisions}"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"ok  {name}")
    print(f"\n{passed} checks passed")
