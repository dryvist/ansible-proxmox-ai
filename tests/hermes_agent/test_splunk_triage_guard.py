"""Self-check for the cron markup guard and the triage job config's own shape.

Split from test_splunk_triage.py to stay under the token budget — see
_splunk_triage_shared.py for the loaded template/guard fixtures and
test_splunk_triage_novelty.py for the per-day novelty and escalation-ladder
contract this leaves behind.

Runs bare (`python3 tests/hermes_agent/test_splunk_triage_guard.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
import itertools
from pathlib import Path

from _role_files import role_defaults
from _splunk_triage_shared import (
    FIXTURE_CONFIG,
    GUARD,
    JOB,
    KNOWN_INDEXES,
    REPO_ROOT,
    STATE_DIR,
    at,
    load_triage_module,
    rows,
)

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


# --- no cron name may be a substring of another ------------------------------


def test_no_reconciled_cron_name_is_a_substring_of_another_job():
    """Both reconcilers test job existence with `name in cron_list_stdout`.

    A RECONCILED name contained in some other job's name therefore reads as
    already-present, and the drift branch fires `cron remove` for a job that
    does not exist — failing the converge on a fresh guest.

    Caught for real: naming the script job `splunk-error-triage` put it inside
    the paused `splunk-error-triage-v2`, which `cron list --all` still prints.

    Only reconciled names are checked against the full universe of names that
    can appear in that listing. There is no Kanban card fleet left to enqueue
    at all (18/18 native-cron reframe), and the superseded-removal list
    matches with exact membership, not substring — not a hazard here.
    """
    defaults = role_defaults(REPO_ROOT / "roles" / "hermes_agent")
    direct = {job["name"] for job in defaults["hermes_agent_direct_cron_jobs"]}
    triage = {job["name"] for job in defaults["hermes_agent_triage_jobs"]}
    # hermes_agent_kanban_safety_net_cron_name is DELETED (native-cron
    # reframe removed the whole safety-net job); 'kanban-enqueue-safety-net'
    # survives only as a literal in hermes_agent_superseded_kanban_enqueuer_cron_names
    # (a name to remove-if-present, never reconciled), so it belongs in the
    # universe set below, not in the reconciled set here.
    scripts = triage | {
        defaults["hermes_agent_splunk_status_digest_cron_name"],
        defaults["hermes_agent_kanban_digest_cron_name"],
    }
    reconciled = direct | scripts
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
    defaults = role_defaults(REPO_ROOT / "roles" / "hermes_agent")
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


def test_no_configured_index_is_a_typo():
    """Catches a misspelled index, which silently kills that part of a search.

    It does NOT catch the defect that actually bit us: `network` is spelled
    correctly and exists, it just has had no data in over a month. Whether an
    index is still being written to is a live question and cannot be answered
    from the repo — this only rules out names that are not indexes at all.
    """
    defaults = role_defaults(REPO_ROOT / "roles" / "hermes_agent")
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
