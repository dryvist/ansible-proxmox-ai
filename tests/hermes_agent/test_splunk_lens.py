"""Self-check for the script-fed Splunk lenses (security + anomaly).

Three contracts, all enforced here:

1. Per-day novelty — a steady stream is presented ONCE per UTC day; NEW,
   ESCALATING and (anomaly lens) GONE streams are critical and repeat every run
   while they hold.
2. Never fabricate — zero rows, an absent baseline and unusable rows are stated
   as themselves, never smoothed into "no anomalies".
3. Never emit tool-call markup — the whole point of removing the LLM from the
   fact path is that nothing this script prints can contain it.

The template's Jinja config lines are rendered to fixtures and the result is
executed, so the thing under test is the code that actually ships.

Runs bare (`python3 tests/hermes_agent/test_splunk_lens.py`) or under pytest.
Plain asserts, no fixtures, no framework.
"""
import datetime as dt
import json
import re
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS_PATH = REPO_ROOT / "roles/hermes_agent/defaults/main.yml"
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/splunk-lens.py.j2"

HERMES_HOME = tempfile.mkdtemp(prefix="splunk-lens-selfcheck-")
JINJA = re.compile(r"\{\{(.+?)\}\}")


def load_defaults():
    import yaml
    return yaml.safe_load(DEFAULTS_PATH.read_text())


DEFAULTS = load_defaults()
PROFILES = DEFAULTS["hermes_agent_splunk_lens_profiles"]
# Only hermes_home is stubbed. Everything else comes from defaults/main.yml, so a
# profile that loses a key the script reads fails here rather than in Slack.
VALUES = {
    "hermes_agent_hermes_home": HERMES_HOME,
    "hermes_agent_splunk_lens_profiles": PROFILES,
    "hermes_agent_splunk_lens_max_findings": DEFAULTS["hermes_agent_splunk_lens_max_findings"],
}


def render_expr(match):
    """Substitute ONE `{{ ... }}` the way Ansible would.

    Rendering the expression in place rather than rewriting the whole assignment
    is the point: `| to_json` emits JSON, and JSON is not Python (`true`/`false`/
    `null`). A stub that pasted a Python repr would hide exactly the bug that
    kills the deployed script at import.
    """
    expr = match.group(1).strip()
    if expr.endswith("| to_json"):
        return json.dumps(VALUES[expr.split("|")[0].strip()])
    return str(VALUES[expr])


def load_lens_module():
    """Render the template as Ansible does and import the result as a module."""
    body = "\n".join(line for line in TEMPLATE_PATH.read_text().splitlines()
                     if "ansible_managed" not in line)
    rendered = JINJA.sub(render_expr, body)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("splunk_lens")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


LENS = load_lens_module()
SECURITY = PROFILES["security"]
ANOMALY = PROFILES["anomaly"]
DAY = dt.datetime(2026, 7, 24, 19, 38, tzinfo=dt.timezone.utc)


def at(hour, day=24):
    return DAY.replace(day=day, hour=hour)


def rows(spec, fields=("host", "sourcetype")):
    """Build stats rows from {first_field_value: {second_field_value: count}}."""
    return [{fields[0]: a, fields[1]: b, "count": str(c)}
            for a, bs in spec.items() for b, c in bs.items()]


def arows(spec):
    return rows(spec, fields=("index", "sourcetype"))


# --- the lens table itself ---------------------------------------------------

def test_every_profile_carries_the_keys_the_script_reads():
    for name, profile in PROFILES.items():
        for key in ("title", "query", "window", "fields", "scope", "noun"):
            assert profile.get(key), f"profile {name} is missing {key}"
        assert len(profile["fields"]) == 2, "the row label joins exactly two fields"
        assert profile["window"].startswith("-"), "window must be a relative Splunk span"


def test_selector_picks_the_lens_and_refuses_to_guess():
    assert LENS.select_profile(["security"])[0] == "security"
    # The cron may pass the positional whole rather than pre-split (same
    # tolerance the kanban enqueuer script has).
    assert LENS.select_profile(["anomaly --whatever"])[0] == "anomaly"
    for bad in ([], [""], ["splunk-security-digest"]):
        try:
            LENS.select_profile(bad)
        except RuntimeError:
            continue
        raise AssertionError(f"selector {bad!r} must not silently pick a lens")


# --- contract 1 + 2: per-day novelty, and never fabricate --------------------

def test_first_run_reports_counts_and_claims_no_change():
    text, state = LENS.build_report(SECURITY, rows({"proxmox1": {"syslog": 400}}), at(1), None)
    assert "proxmox1 / syslog" in text and "400" in text
    assert "NEW" not in text and "ESCALATING" not in text, \
        "nothing can be new against an absent baseline"
    assert state["counts"] == {"proxmox1|syslog": 400}


def test_steady_stream_is_presented_once_per_day():
    data = rows({"proxmox1": {"syslog": 400}})
    text, state = LENS.build_report(SECURITY, data, at(1), None)
    assert "proxmox1 / syslog" in text, "the day's first run presents the stream"
    text, state = LENS.build_report(SECURITY, data, at(2), state)
    assert "proxmox1 / syslog" not in text, "a steady stream must not repeat within the day"
    assert "Nothing new" in text, "the day gets one exhausted-search line"
    assert str(len(LENS.parse_rows(data, SECURITY["fields"]))) in text, \
        "and it names the space it covered"
    text, _ = LENS.build_report(SECURITY, data, at(3), state)
    assert text == LENS.SILENT, "after that, silence for the rest of the day"


def test_a_new_utc_day_resets_the_ledger():
    data = rows({"proxmox1": {"syslog": 400}})
    _, state = LENS.build_report(SECURITY, data, at(1), None)
    _, state = LENS.build_report(SECURITY, data, at(2), state)
    text, state = LENS.build_report(SECURITY, data, at(3), state)
    assert text == LENS.SILENT
    text, _ = LENS.build_report(SECURITY, data, at(1, day=25), state)
    assert "proxmox1 / syslog" in text, "a new UTC day re-presents routine information"


def test_a_new_stream_is_critical_and_repeats():
    _, state = LENS.build_report(SECURITY, rows({"proxmox1": {"syslog": 400}}), at(1), None)
    both = rows({"proxmox1": {"syslog": 400}, "fw01": {"cisco:asa": 90}})
    text, state = LENS.build_report(SECURITY, both, at(2), state)
    assert "NEW auth-failure stream fw01 / cisco:asa" in text
    text, state = LENS.build_report(SECURITY, both, at(3), state)
    assert "fw01 / cisco:asa" in text, "critical findings ignore the ledger"


def test_an_order_of_magnitude_climb_is_escalating():
    _, state = LENS.build_report(SECURITY, rows({"fw01": {"cisco:asa": 90}}), at(1), None)
    text, _ = LENS.build_report(SECURITY, rows({"fw01": {"cisco:asa": 3100}}), at(2), state)
    assert "ESCALATING" in text and "3.1k" in text and "90" in text


def test_jitter_within_a_band_is_not_an_escalation():
    _, state = LENS.build_report(SECURITY, rows({"fw01": {"cisco:asa": 400}}), at(1), None)
    text, _ = LENS.build_report(SECURITY, rows({"fw01": {"cisco:asa": 900}}), at(2), state)
    assert "ESCALATING" not in text, "same order of magnitude is normal jitter"


def test_zero_rows_is_stated_not_smoothed_into_all_clear():
    text, state = LENS.build_report(SECURITY, [], at(1), None)
    assert "Zero rows" in text and "ingest has stopped" in text
    assert "no anomalies" not in text.lower()
    text, _ = LENS.build_report(SECURITY, [], at(2), state)
    assert text == LENS.SILENT, "routine zero-rows is once per day too"


def test_findings_over_the_cap_are_not_ledgered():
    """A novel finding that did not fit this run must surface on the next one."""
    many = rows({f"host{i:02d}": {"syslog": 500 - i} for i in range(LENS.MAX_FINDINGS + 3)})
    _, state = LENS.build_report(SECURITY, many, at(1), None)
    text, _ = LENS.build_report(SECURITY, many, at(2), state)
    dropped = [f"host{i:02d}" for i in range(LENS.MAX_FINDINGS, LENS.MAX_FINDINGS + 3)]
    assert any(h in text for h in dropped), \
        "a finding cut by the output cap was ledgered as if it had been posted"


def test_unusable_rows_are_dropped_not_guessed():
    noisy = rows({"proxmox1": {"syslog": 400}}) + [
        {"host": "", "sourcetype": "syslog", "count": "5"},
        {"host": "x", "sourcetype": "y", "count": "not-a-number"},
        {"host": "z", "sourcetype": "w", "count": "0"},
    ]
    _, state = LENS.build_report(SECURITY, noisy, at(1), None)
    assert state["counts"] == {"proxmox1|syslog": 400}


def test_an_older_state_schema_is_treated_as_no_baseline():
    stale = {"schema": LENS.STATE_SCHEMA - 1, "counts": {"proxmox1|syslog": 5}}
    text, _ = LENS.build_report(SECURITY, rows({"proxmox1": {"syslog": 400}}), at(1), stale)
    assert "ESCALATING" not in text and "NEW" not in text


# --- what makes the two lenses different -------------------------------------

def test_security_lens_ranks_the_loudest_stream_first():
    data = rows({"quiet": {"syslog": 5}, "loud": {"syslog": 9000}})
    text, _ = LENS.build_report(SECURITY, data, at(1), None)
    assert text.index("loud / syslog") < text.index("quiet / syslog")


def test_anomaly_lens_ranks_the_rarest_stream_first():
    """The long tail IS the 'pattern nobody monitors' — the loud head is already
    the status digest's job, so surfacing it here would just duplicate that."""
    data = arows({"quiet": {"syslog": 5}, "loud": {"syslog": 9000}})
    text, _ = LENS.build_report(ANOMALY, data, at(1), None)
    assert text.index("quiet / syslog") < text.index("loud / syslog")


def test_a_vanished_stream_is_reported_and_flagged_on_the_anomaly_lens():
    """A source falling silent was the old anomaly-hunt's stated mission, so the
    anomaly lens flags it; on the security lens a host that STOPPED failing auth
    is good news and stays unflagged.

    Reported ONCE by construction: the vanished pair is absent from the counts
    this run saves, so the next run has nothing to miss it from. Standing ingest
    silence is splunk-digest.py's job and repeats there.
    """
    before = arows({"proxmox1": {"syslog": 400}, "quiet": {"audit": 300}})
    _, state = LENS.build_report(ANOMALY, before, at(1), None)
    after = arows({"proxmox1": {"syslog": 400}})
    text, state = LENS.build_report(ANOMALY, after, at(2), state)
    assert ":warning: quiet / audit GONE" in text, "the anomaly lens flags it"
    assert "quiet|audit" not in state["counts"], "and stops carrying it"
    text, _ = LENS.build_report(ANOMALY, after, at(3), state)
    assert "quiet / audit" not in text, "a discrete event is reported once"

    _, state = LENS.build_report(SECURITY, rows({"proxmox1": {"syslog": 400},
                                                 "quiet": {"audit": 300}}), at(1), None)
    text, _ = LENS.build_report(SECURITY, rows({"proxmox1": {"syslog": 400}}), at(2), state)
    assert "quiet / audit GONE" in text and ":warning:" not in text, \
        "reported, but not flagged as an alert"


def test_the_two_lenses_keep_separate_state_files():
    assert LENS.state_path("security") != LENS.state_path("anomaly")


# --- contract 3: nothing this script prints can be tool-call markup ----------

def test_delivered_text_never_contains_tool_call_markup():
    """The failure being fixed: the agentic jobs delivered `<function=...>` text."""
    markup = re.compile(r"<(?:function|parameter)=|<tool_call>")
    for profile, data in ((SECURITY, rows({"proxmox1": {"syslog": 400}})),
                          (ANOMALY, arows({"main": {"syslog": 400}})),
                          (SECURITY, []),
                          (ANOMALY, [])):
        text, _ = LENS.build_report(profile, data, at(1), None)
        assert not markup.search(text)


# --- no cron name may be a substring of another ------------------------------

def test_no_reconciled_cron_name_is_a_substring_of_another_job():
    """The enqueuer reconciler tests job existence with `name in cron_list_stdout`.

    A RECONCILED name contained in some other job's name therefore reads as
    already-present, and the drift branch fires `cron remove` for a job that does
    not exist — failing the converge on a fresh guest. `cron list --all` prints
    paused jobs too, so a superseded name is just as hazardous as a live one.

    That is why the lenses are not named `splunk-security-lens` (inside the
    paused `splunk-security-lens-v2`) or `anomaly-hunt` (an existing kanban job).

    Only names actually RECONCILED are hazardous. A paused card and a disabled
    direct job are skipped whole (no lookup, no create, no remove), so they can
    only ever appear on the right-hand side. `review` and `zammad-review` are
    both paused today and collide as `review-enqueue` / `zammad-review-enqueue` —
    dormant, but it becomes a live converge failure the moment either is
    unpaused, so it is called out here rather than left to be rediscovered.
    """
    import itertools

    def resolve(value):
        """A kanban card names its job by `{{ var }}`; deref so this compares real
        names. Without it the set holds literal Jinja and catches nothing."""
        match = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", value.strip())
        resolved = DEFAULTS.get(match.group(1)) if match else value
        assert isinstance(resolved, str) and "{{" not in resolved, \
            f"cron name {value!r} did not resolve to a literal"
        return resolved

    paused = {resolve(job) for job in DEFAULTS["hermes_agent_kanban_paused_jobs"]}
    direct = {job["name"] for job in DEFAULTS["hermes_agent_direct_cron_jobs"]
              if job.get("enabled")}
    enqueuers = {resolve(card["job"]) + "-enqueue"
                 for card in DEFAULTS.get("hermes_agent_kanban_cards", [])
                 if resolve(card["job"]) not in paused}
    scripts = {
        DEFAULTS["hermes_agent_splunk_lens_security_cron_name"],
        DEFAULTS["hermes_agent_splunk_lens_anomaly_cron_name"],
        DEFAULTS["hermes_agent_splunk_status_digest_cron_name"],
        DEFAULTS["hermes_agent_kanban_safety_net_cron_name"],
    }
    reconciled = (direct | enqueuers | scripts) - paused
    # Everything `cron list --all` can print, including paused/superseded jobs.
    universe = (direct | scripts
                | {resolve(card["job"]) + "-enqueue"
                   for card in DEFAULTS.get("hermes_agent_kanban_cards", [])}
                | {job["name"] for job in DEFAULTS["hermes_agent_direct_cron_jobs"]}
                | {v for k, v in DEFAULTS.items()
                   if k.endswith("_cron_name") and isinstance(v, str)})
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
