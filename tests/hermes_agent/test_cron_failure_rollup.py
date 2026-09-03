"""Self-check for the hourly cron failure rollup (cron-failure-rollup.py.j2).

Renders the shipped template with fixture config and runs it as a module, so
these assertions exercise the real script. Runs bare or under pytest.
"""
import json
import re
import runpy
import tempfile
import types
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


def test_causes_are_classed_from_the_error_text():
    assert MOD.cause_of("Cron job exceeded wall-clock budget of 1800s") == "wall-clock"
    assert MOD.cause_of("HTTP 401 Unauthorized from splunk") == "auth"
    assert MOD.cause_of("litellm.BadGatewayError: 502") == "upstream-5xx"
    assert MOD.cause_of("OpenRouter: insufficient credits (budget)") == "budget"
    assert MOD.cause_of("") == "unknown"
    assert MOD.cause_of("something novel happened here") == "something novel happened here"


def test_every_store_is_read_and_only_failing_enabled_jobs_are_kept():
    write_store(TMP, [job("ok", status="ok", streak=0), job("bad", error="502"),
                      job("off", enabled=False, error="502")])
    write_store(TMP / "profiles" / "splunk-admin", [job("triage", streak=12, error="wall-clock kill")])
    failing = MOD.failing_jobs((n, MOD.load_jobs(h)) for n, h in MOD.stores())
    assert failing == [("default", "bad", 1, "upstream-5xx"),
                       ("splunk-admin", "triage", 12, "wall-clock")]


def test_the_message_groups_by_cause_and_names_the_streak():
    failing = [("default", "a", 1, "auth"), ("default", "b", 5, "auth"), ("p", "c", 31, "wall-clock")]
    text = MOD.build_message(failing)
    assert text.splitlines()[0] == ":rotating_light: 3 cron job(s) failing"
    assert "• auth (2): a, b ×5" in text
    assert "• wall-clock (1): p/c ×31" in text


def test_an_unchanged_set_reposts_only_after_the_heartbeat():
    failing = [("default", "a", 1, "auth")]
    text, state = MOD.decide(failing, {}, NOW)
    assert text and state["signature"] == ["default/a:auth"]
    assert MOD.decide(failing, state, NOW + 3600)[0] is None
    assert MOD.decide(failing, state, NOW + 6 * 3600)[0] is not None
    # A streak change alone is not news; a new job or cause is.
    assert MOD.decide([("default", "a", 9, "auth")], state, NOW + 3600)[0] is None
    assert MOD.decide([("default", "a", 1, "budget")], state, NOW + 3600)[0] is not None


def test_the_all_clear_posts_once_when_the_set_empties():
    _, state = MOD.decide([("default", "a", 1, "auth")], {}, NOW)
    text, state = MOD.decide([], state, NOW + 3600)
    assert text.startswith(":white_check_mark:")
    assert MOD.decide([], state, NOW + 7200) == (None, state)


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
