"""Contract checks for the Vikunja backlog-review and session-follow-up crons.

Both jobs are declared as data in the role's split defaults (149-*.yml) and
reassembled into hermes_agent_direct_cron_jobs by 43-direct-cron-jobs-core.yml.
The checks below pin the parts a later edit would silently break: the ten
per-project backlog jobs and their minutes, the profile each one runs on, the
advisory-only wording of the prompt, and the two list names the aggregate
concatenation must keep naming.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS_DIR = ROLE_ROOT / "defaults" / "main"
NEW_DEFAULTS = DEFAULTS_DIR / "149-backlog-and-history-crons.yml"
CORE_DEFAULTS = DEFAULTS_DIR / "43-direct-cron-jobs-core.yml"

BACKLOG_LIST = "hermes_agent_direct_cron_jobs_vikunja_backlog"
FOLLOWUPS_LIST = "hermes_agent_direct_cron_jobs_session_followups"
PROJECT_COUNT = 10
# Minutes the board digest and the :00/:30 pair are avoided on.
FORBIDDEN_MINUTES = {0, 15, 30, 45}


def _backlog_jobs() -> list[dict]:
    defaults = role_defaults(ROLE_ROOT)
    return defaults[BACKLOG_LIST]


def test_new_defaults_file_parses_and_declares_both_lists() -> None:
    parsed = yaml.safe_load(NEW_DEFAULTS.read_text())
    assert isinstance(parsed, dict)
    assert BACKLOG_LIST in parsed
    assert FOLLOWUPS_LIST in parsed
    assert "hermes_agent_vikunja_backlog_review_enabled" in parsed
    assert "hermes_agent_vikunja_backlog_review_cron_prompt" in parsed
    assert "hermes_agent_session_followups_cron_prompt" in parsed


def test_there_are_exactly_ten_backlog_projects() -> None:
    jobs = _backlog_jobs()
    assert len(jobs) == PROJECT_COUNT
    names = [job["name"] for job in jobs]
    assert all(name.startswith("vikunja-backlog-") for name in names)
    assert len(set(names)) == PROJECT_COUNT


def test_every_backlog_schedule_is_odd_off_quarter_and_never_shares_a_start() -> None:
    starts: set[tuple[int, int]] = set()
    for job in _backlog_jobs():
        fields = job["schedule"].split()
        assert len(fields) == 5, job["name"]
        minute = int(fields[0])
        assert minute % 2 == 1, f"{job['name']} fires on an even minute: {minute}"
        assert minute not in FORBIDDEN_MINUTES, f"{job['name']} fires on :{minute:02d}"
        hours = [int(h) for h in fields[1].split(",")]
        assert len(hours) == 6, f"{job['name']} must run every 4 hours"
        for hour in hours:
            assert (hour, minute) not in starts, f"{job['name']} shares {hour:02d}:{minute:02d}"
            starts.add((hour, minute))


def test_every_backlog_job_runs_on_the_vikunja_pm_profile() -> None:
    for job in _backlog_jobs():
        assert job["hermes_home"].endswith("/profiles/vikunja-pm"), job["name"]


def test_every_backlog_job_delivers_locally() -> None:
    for job in _backlog_jobs():
        assert job["deliver"] == "local", job["name"]
        assert job["failure_deliver"] == "local", job["name"]


def test_backlog_gate_is_the_vikunja_pm_profile_key() -> None:
    gate = role_defaults(ROLE_ROOT)["hermes_agent_vikunja_backlog_review_enabled"]
    assert "llm_router_hermes_vikunja_pm_key_enabled" in gate
    assert "hermes_agent_id == 'hermes'" in gate


def test_backlog_prompt_stays_advisory_only() -> None:
    prompt = " ".join(
        role_defaults(ROLE_ROOT)["hermes_agent_vikunja_backlog_review_cron_prompt"].split()
    )
    assert "never change any task field" in prompt
    assert "[SILENT]" in prompt


def test_both_new_lists_are_named_by_the_aggregate_concatenation() -> None:
    core_text = CORE_DEFAULTS.read_text()
    assert BACKLOG_LIST in core_text
    assert FOLLOWUPS_LIST in core_text
    defaults = role_defaults(ROLE_ROOT)
    names = {job["name"] for job in defaults["hermes_agent_direct_cron_jobs"]}
    assert {job["name"] for job in defaults[BACKLOG_LIST]} <= names
    assert "session-followups" in names


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
