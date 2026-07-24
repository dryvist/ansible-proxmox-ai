"""Self-check for the script-fed Splunk triage digests and the cron markup guard.

Two contracts, both enforced here:

1. Never deliver tool-call markup — the guard patched into cron/scheduler.py
   replaces any `<function=...>` / `<parameter=...>` / `<tool_call>` response
   with a diagnostic naming the job. The guard body lives in a blockinfile in
   roles/hermes_agent/tasks/main.yml, so it is extracted from there and executed:
   the thing under test is the code that actually ships.
2. Per-day novelty — a steady stream is presented ONCE per UTC day. Findings
   are deltas against the PREVIOUS run, so the baseline advances and no finding
   restates itself; `critical` means "bypass the ledger", which shows up when
   the same delta RECURS within a day, not as a line repeating every run.
   Never fabricate: zero rows and an absent baseline are stated as themselves.

Runs bare (`python3 tests/hermes_agent/test_splunk_triage.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
import datetime as dt
import logging
import re
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/splunk-triage.py.j2"
TASKS_PATH = REPO_ROOT / "roles/hermes_agent/tasks/main.yml"

STATE_DIR = tempfile.mkdtemp(prefix="splunk-triage-selfcheck-")
# Stand-ins for the values Ansible renders from roles/hermes_agent/defaults/main.yml.
FIXTURE_CONFIG = {
    "ENV_PATH": str(Path(STATE_DIR) / "unused.env"),
    "STATE_PATH": str(Path(STATE_DIR) / "splunk-error-digest.json"),
    "TITLE": "Splunk error triage",
    "INDEXES": ["os"],
    "TERMS": ["error", "failed", "critical"],
    "EARLIEST": "-1h",
    "TOP_N": 12,
    "MAX_FINDINGS": 8,
}


def load_triage_module(config=None):
    """Render the template's config lines to fixtures and import it as a module.

    `config` overrides the defaults so one template can be exercised as any of
    the jobs in hermes_agent_triage_jobs.
    """
    config = config or FIXTURE_CONFIG
    out = []
    for line in TEMPLATE_PATH.read_text().splitlines():
        if "ansible_managed" in line:
            continue
        match = re.match(r"^(\w+) = .*\{\{", line)
        if match:
            name = match.group(1)
            assert name in config, f"template config {name} has no self-check fixture"
            out.append(f"{name} = {config[name]!r}")
            continue
        out.append(line)
    rendered = "\n".join(out)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("splunk_triage")
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
    # Seed the exec namespace directly: the guard resolves `re` and `logger` as
    # module globals, which is a dict operation, not attribute assignment.
    mod.__dict__["re"] = re
    mod.__dict__["logger"] = logging.getLogger("selfcheck")
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
    assert "No new signatures" in text, "the day gets one exhausted-search line"
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


def test_a_new_source_is_flagged_then_tracked_as_steady():
    _, state = TRIAGE.build_report(rows({"pve3": {"syslog": 400}}), at(1), None)
    both = rows({"pve3": {"syslog": 400}, "fw01": {"cisco:asa": 90}})
    text, state = TRIAGE.build_report(both, at(2), state)
    assert "NEW source fw01 / cisco:asa" in text
    text, state = TRIAGE.build_report(both, at(3), state)
    assert "fw01 / cisco:asa" in text and "NEW source" not in text, \
        "once the baseline knows it, it is a steady stream, not a new one"


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
    triage = {job["name"] for job in defaults["hermes_agent_triage_jobs"]}
    scripts = triage | {
        defaults["hermes_agent_splunk_status_digest_cron_name"],
        defaults["hermes_agent_kanban_safety_net_cron_name"],
    }
    reconciled = direct | enqueuers | scripts
    # Everything `cron list --all` can print, including paused/superseded jobs.
    universe = (reconciled
                | {job["supersedes"] for job in defaults["hermes_agent_triage_jobs"]}
                | {v for k, v in defaults.items()
                   if k.endswith("_cron_name") and isinstance(v, str)})
    collisions = [(r, n) for r, n in itertools.product(sorted(reconciled), sorted(universe))
                  if r != n and r in n]
    assert not collisions, f"reconciled cron name(s) contained in another job's name: {collisions}"


def test_every_configured_triage_job_renders_and_is_distinct():
    """Adding a job is config, so the config is what gets checked.

    Each entry must render to a runnable script with its own SPL and its own
    state file — two jobs sharing a state path would share a baseline and a
    novelty ledger, and silently suppress each other's findings.
    """
    import yaml

    defaults = yaml.safe_load((REPO_ROOT / "roles/hermes_agent/defaults/main.yml").read_text())
    jobs = defaults["hermes_agent_triage_jobs"]
    assert len(jobs) >= 2, "expected at least the error and security digests"

    seen_state, seen_spl = {}, {}
    for job in jobs:
        for field in ("name", "title", "schedule", "indexes", "terms", "window", "supersedes"):
            assert job.get(field), f"{job.get('name', '?')} is missing {field}"
        mod = load_triage_module({
            **FIXTURE_CONFIG,
            "STATE_PATH": str(Path(STATE_DIR) / f"{job['name']}.json"),
            "TITLE": job["title"],
            "INDEXES": job["indexes"],
            "TERMS": job["terms"],
            "EARLIEST": job["window"],
        })
        for term in job["terms"]:
            assert term in mod.SPL, f"{job['name']} SPL omits term {term}"
        assert mod.TITLE in mod.build_report(
            rows({"h": {"syslog": 5}}), at(1), None)[0], "title must head the report"
        assert job["name"] not in seen_state, "duplicate job name"
        seen_state[job["name"]] = mod.STATE_PATH
        assert mod.SPL not in seen_spl, f"{job['name']} duplicates {seen_spl.get(mod.SPL)}'s SPL"
        seen_spl[mod.SPL] = job["name"]
    assert len(set(seen_state.values())) == len(jobs), "jobs must not share a state file"


# Indexes that exist in the homelab Splunk, from
# `| eventcount summarize=false index=*`, which ENUMERATES indexes. Do not
# derive this from `tstats`: that only returns indexes with data in the search
# window, so it silently omits real-but-idle ones and would make this check
# reject valid config. A job searching an index that does not exist returns
# nothing for that part of its search and looks entirely healthy doing it —
# which is what `index=network` did in the prompts these jobs replace. Refresh
# this list when an index is genuinely added; do not add a name to make a test
# pass.
KNOWN_INDEXES = {
    "ai", "claude", "codex", "dns", "firewall", "gemini", "genai_traces",
    "hermes", "history", "honeypot", "host_metrics", "llm", "llm_metrics",
    "mac_perf", "main", "netflow", "netmon_metrics", "network", "openai",
    "openbao_audit", "os", "os_metrics", "otel", "proxy", "summary", "unifi",
    "unifi_metrics", "vscode",
}


# --- escalation ladder: tier 2 (hosts) and tier 3 (sourcetype mix) ----------
# Tier 1 buckets by order of magnitude; the ladder buckets by percent. That is
# what makes them complementary: a +60% move keeps the same OOM bucket, so tier
# 1 has nothing new to say about it while tier 2 does.

def test_ladder_fires_only_when_tier_one_is_exhausted():
    first = rows({"a": {"syslog": 100}})
    text, state = TRIAGE.build_report(first, at(1), None)
    assert "a / syslog" in text
    text, _ = TRIAGE.build_report(rows({"a": {"syslog": 160}}), at(2), state)
    assert "Host a up +60%" in text, "a percent move tier 1 cannot see must escalate"
    assert "No new signatures" not in text


def test_tier_one_content_suppresses_the_ladder():
    _, state = TRIAGE.build_report(rows({"a": {"syslog": 100}}), at(1), None)
    text, _ = TRIAGE.build_report(
        rows({"a": {"syslog": 100}, "b": {"syslog": 90}}), at(2), state)
    assert "NEW source b" in text
    assert "Host " not in text, "the ladder is a fallback, not an addition"


def test_a_moderate_climb_is_routine_and_does_not_repeat():
    _, state = TRIAGE.build_report(rows({"a": {"syslog": 100}}), at(1), None)
    later = rows({"a": {"syslog": 160}})
    text, state = TRIAGE.build_report(later, at(2), state)
    assert "Host a up +60%" in text
    text, _ = TRIAGE.build_report(later, at(3), state)
    assert "Host a" not in text, "a band-edge wobble must not repeat all day"


def test_a_climb_is_reported_once_because_the_baseline_catches_up():
    """Ladder findings are deltas against the PREVIOUS run, so no ladder finding
    can persist: once the baseline advances, the move is gone. That is why none
    of them are marked critical — a critical delta could not repeat a real
    escalation, only a band-edge oscillation."""
    _, state = TRIAGE.build_report(rows({"a": {"syslog": 100}}), at(1), None)
    later = rows({"a": {"syslog": 500}})
    text, state = TRIAGE.build_report(later, at(2), state)
    assert "Host a up +400%" in text
    text, _ = TRIAGE.build_report(later, at(3), state)
    assert "Host a" not in text, "500 -> 500 is not a move"


def test_an_oscillating_host_does_not_re_post_the_same_move_all_day():
    quiet, loud = rows({"a": {"syslog": 100}}), rows({"a": {"syslog": 500}})
    _, state = TRIAGE.build_report(quiet, at(1), None)
    text, state = TRIAGE.build_report(loud, at(2), state)
    assert "Host a up +400%" in text
    _, state = TRIAGE.build_report(quiet, at(3), state)
    text, _ = TRIAGE.build_report(loud, at(4), state)
    assert "Host a up +400%" not in text, "the same swing must not re-post today"


def test_a_host_that_stopped_reporting_is_surfaced():
    """Tier 1 only walks CURRENT streams, so a disappearance is information it
    structurally cannot produce."""
    _, state = TRIAGE.build_report(
        rows({"a": {"syslog": 100}, "b": {"syslog": 100}}), at(1), None)
    text, _ = TRIAGE.build_report(rows({"a": {"syslog": 100}}), at(2), state)
    assert "Stopped reporting: b" in text


def test_a_share_shift_is_critical_even_when_volume_is_flat():
    """The mix changing is how a new failure mode announces itself while totals
    stay put — the thing a volume-only view misses."""
    before = TRIAGE.parse_rows(rows({"a": {"syslog": 100, "audit": 100}}))
    after = TRIAGE.parse_rows(rows({"a": {"syslog": 40, "audit": 160}}))
    shares = [f for f in TRIAGE.composition_findings(after, before, True)
              if f.key.startswith("share:")]
    assert shares, "a 30-point mix shift must be a finding"
    assert all(f.critical for f in shares)


def test_a_move_too_small_to_mean_anything_is_not_a_finding():
    assert TRIAGE.move_of(11, 10) is None, "series under MIN_MOVER_COUNT"
    assert TRIAGE.move_of(105, 100) is None, "swing under the smallest band"
    assert TRIAGE.move_of(100, None) is None, "no baseline"


def test_ladder_findings_never_contain_tool_call_markup():
    _, state = TRIAGE.build_report(rows({"a": {"syslog": 100}}), at(1), None)
    text, _ = TRIAGE.build_report(rows({"a": {"syslog": 500}}), at(2), state)
    assert GUARD(JOB, "out.md", text) == text


def test_no_configured_index_is_a_typo():
    """Catches a misspelled index, which silently kills that part of a search.

    It does NOT catch the defect that actually bit us: `network` is spelled
    correctly and exists, it just has had no data in over a month. Whether an
    index is still being written to is a live question and cannot be answered
    from the repo — this only rules out names that are not indexes at all.
    """
    import yaml

    defaults = yaml.safe_load((REPO_ROOT / "roles/hermes_agent/defaults/main.yml").read_text())
    for job in defaults["hermes_agent_triage_jobs"]:
        unknown = set(job["indexes"]) - KNOWN_INDEXES
        assert not unknown, (
            f"{job['name']} searches unknown index(es) {sorted(unknown)} — "
            f"that part of its search silently matches nothing")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"ok  {name}")
    print(f"\n{passed} checks passed")
