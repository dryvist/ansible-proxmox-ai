"""Self-check for the hourly cron failure rollup (cron-failure-rollup.py.j2).

Renders the shipped template with fixture config and runs it as a module, so
these assertions exercise the real script. Runs bare or under pytest.
"""
import json
import re
import runpy
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

from _role_files import role_defaults, role_tasks_text

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
TEMPLATE_PATH = ROLE / "templates" / "cron-failure-rollup.py.j2"
TMP = Path(tempfile.mkdtemp(prefix="cron-failure-rollup-selfcheck-"))

FIXTURE = {
    "HERMES_HOME": f'Path("{TMP}")',
    "STATE_PATH": f'Path("{TMP}/state/rollup.json")',
    "HEARTBEAT_HOURS": "6",
    "ISSUES_MARKER": '"[ISSUES]"',
}


def load_module():
    out = []
    for line in TEMPLATE_PATH.read_text().splitlines():
        if "ansible_managed" in line:
            continue
        m = re.match(r"^(\w+) = .*\{\{", line)
        if m:
            out.append(f"{m.group(1)} = {FIXTURE[m.group(1)]}")
            continue
        out.append(line)
    rendered = "\n".join(out)
    assert "{{" not in rendered
    path = TMP / "cron-failure-rollup.py"
    path.write_text(rendered)
    return types.SimpleNamespace(**runpy.run_path(str(path), run_name="rollup"))


MOD = load_module()
NOW = 1_785_000_000.0


def write_store(home, jobs):
    (home / "cron").mkdir(parents=True, exist_ok=True)
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}))


def job(name, status="error", streak=1, error="", enabled=True):
    return {"name": name, "last_status": status, "failure_streak": streak,
            "last_error": error, "enabled": enabled}


def row(store, name, streak, cause, age=None, stale=False):
    """One failing-job tuple, as failing_jobs returns it."""
    return (store, name, streak, cause, age, stale)


def timed(name, *, last_ago_h, cadence_h, error="HTTP 429"):
    """A failing job whose scheduler bookkeeping puts its last run last_ago_h hours back."""
    last = NOW - last_ago_h * 3600
    return {**job(name, error=error),
            "last_run_at": datetime.fromtimestamp(last, timezone.utc).isoformat(),
            "next_run_at": datetime.fromtimestamp(last + cadence_h * 3600, timezone.utc).isoformat()}


def test_causes_are_classed_from_the_error_text():
    assert MOD.cause_of("Cron job exceeded wall-clock budget of 1800s") == "wall-clock"
    assert MOD.cause_of("HTTP 401 Unauthorized from splunk") == "auth"
    assert MOD.cause_of("litellm.BadGatewayError: 502") == "upstream-5xx"
    assert MOD.cause_of("OpenRouter: insufficient credits (budget)") == "budget"
    assert MOD.cause_of("") == "unknown"
    assert MOD.cause_of("something novel happened here") == "something novel happened here"


def test_the_router_provider_cap_is_its_own_class_not_a_key_budget():
    cap = ("No deployments available - crossed budget: "
           "Exceeded budget for provider example-provider: 12.5 >= 12.0")
    assert MOD.cause_of(cap) == "provider-cap"
    assert MOD.cause_of("Exceeded budget for provider example-provider") == "provider-cap"
    assert MOD.cause_of("insufficient credits (budget)") == "budget"


def test_every_store_is_read_and_only_failing_enabled_jobs_are_kept():
    write_store(TMP, [job("ok", status="ok", streak=0), job("bad", error="502"),
                      job("off", enabled=False, error="502")])
    write_store(TMP / "profiles" / "splunk-admin", [job("triage", streak=12, error="wall-clock kill")])
    failing = MOD.failing_jobs(((n, MOD.load_jobs(h)) for n, h in MOD.stores()), NOW)
    assert failing == [row("default", "bad", 1, "upstream-5xx"),
                       row("splunk-admin", "triage", 12, "wall-clock")]


def test_the_message_groups_by_cause_and_names_the_streak():
    failing = [row("default", "a", 1, "auth"), row("default", "b", 5, "auth"), row("p", "c", 31, "wall-clock")]
    text = MOD.build_message(failing)
    assert text.splitlines()[0] == ":rotating_light: 3 cron job(s) failing"
    assert "• auth (2): a, b ×5" in text
    assert "• wall-clock (1): p/c ×31" in text


def test_a_job_past_its_cadence_is_stale_not_currently_failing():
    jobs = [timed("fresh", last_ago_h=9, cadence_h=24),
            timed("overdue", last_ago_h=50, cadence_h=24),
            timed("hourly", last_ago_h=5, cadence_h=1),
            job("untimed", error="HTTP 429")]
    failing = MOD.failing_jobs([("default", jobs)], NOW)
    assert [(r[1], r[5]) for r in failing] == [
        ("fresh", False), ("hourly", True), ("overdue", True), ("untimed", False)]
    assert MOD.build_message(failing).splitlines() == [
        ":rotating_light: 2 cron job(s) failing",
        "• rate-limit (2): fresh (last 9h ago), untimed",
        "• stale, last run past cadence (2): hourly (last 5h ago), overdue (last 2d ago)",
    ]
    # A job going stale is news even though no name or cause changed.
    _, state = MOD.decide([row("default", "a", 1, "auth")], {}, NOW)
    assert MOD.decide([row("default", "a", 1, "auth", age=50 * 3600, stale=True)], state, NOW + 60)[0]


def test_causes_are_cut_at_80_characters_on_a_word_boundary():
    short = "RuntimeError: HTTP 400: Missing consumer trace data for run 42"
    assert len(short) <= 80 and MOD.cause_of(short) == short
    long = ("RuntimeError: HTTP 400: Missing consumer trace data for run 42 because the router "
            "requires the telemetry tag set on every request")
    label = MOD.cause_of(long)
    assert label.endswith("...") and len(label) <= 83
    kept = label[:-3]
    assert long.startswith(kept) and long[len(kept)] == " "


def test_an_unchanged_set_reposts_only_after_the_heartbeat():
    failing = [row("default", "a", 1, "auth")]
    text, state = MOD.decide(failing, {}, NOW)
    assert text and state["signature"] == ["default/a:auth"]
    assert MOD.decide(failing, state, NOW + 3600)[0] is None
    assert MOD.decide(failing, state, NOW + 6 * 3600)[0] is not None
    # A streak change alone is not news; a new job or cause is.
    assert MOD.decide([row("default", "a", 9, "auth")], state, NOW + 3600)[0] is None
    assert MOD.decide([row("default", "a", 1, "budget")], state, NOW + 3600)[0] is not None


def test_a_zero_heartbeat_never_reposts_an_unchanged_set():
    failing = [row("default", "a", 1, "auth")]
    _, state = MOD.decide(failing, {}, NOW)
    module_globals = MOD.decide.__globals__
    saved = module_globals["HEARTBEAT_HOURS"]
    module_globals["HEARTBEAT_HOURS"] = 0
    try:
        assert MOD.decide(failing, state, NOW + 30 * 24 * 3600)[0] is None
    finally:
        module_globals["HEARTBEAT_HOURS"] = saved


def test_the_all_clear_posts_once_when_the_set_empties():
    _, state = MOD.decide([row("default", "a", 1, "auth")], {}, NOW)
    text, state = MOD.decide([], state, NOW + 3600)
    assert text.startswith(":white_check_mark:")
    assert MOD.decide([], state, NOW + 7200) == (None, state)


def test_the_memory_breaker_is_read_from_the_newest_output_and_posted_with_its_own_signature():
    outputs = TMP / "profiles" / "splunk-admin" / "cron" / "output" / "abc123"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "old.md").write_text("## Response\nMemory is blocked after 4 failures. I'll proceed.")
    (outputs / "new.md").write_text("## Response\nAll good, memory saved.")
    import os
    os.utime(outputs / "old.md", (NOW - 7200, NOW - 7200))
    os.utime(outputs / "new.md", (NOW - 60, NOW - 60))
    assert MOD.memory_blocked_jobs(NOW) == []
    (outputs / "new.md").write_text("## Response\nMemory is blocked after 4 failures. Baseline may be stale.")
    os.utime(outputs / "new.md", (NOW - 60, NOW - 60))
    assert MOD.memory_blocked_jobs(NOW) == ["splunk-admin/abc123"]
    # A day-old hit is not current.
    os.utime(outputs / "new.md", (NOW - 30 * 3600, NOW - 30 * 3600))
    os.utime(outputs / "old.md", (NOW - 31 * 3600, NOW - 31 * 3600))
    assert MOD.memory_blocked_jobs(NOW) == []
    text, state = MOD.decide([], {}, NOW, ["splunk-admin/abc123"])
    assert text.startswith(":brain: memory breaker tripped") and "abc123" in text
    assert state["signature"] == ["memory:splunk-admin/abc123"]
    assert MOD.decide([], state, NOW + 3600, ["splunk-admin/abc123"])[0] is None
    assert MOD.decide([], state, NOW + 3600, [])[0].startswith(":white_check_mark:")


def test_the_cron_is_registered_deployed_and_documented():
    defaults = role_defaults(ROLE)
    assert defaults["hermes_agent_cron_failure_rollup_schedule"] == "7 * * * *"
    assert "Reconcile the cron failure rollup cron" in role_tasks_text(ROLE, "cron_reconcile.yml")
    assert "Deploy the cron failure rollup script" in role_tasks_text(ROLE, "script_deploys.yml")
    assert "`cron-failure-rollup`" in (REPO_ROOT / "docs/hermes-ops/cron-fleet.md").read_text()


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
