"""Self-checks for the bounded daily Hermes backup outcome query."""
from _splunk_digest_shared import BASE, DIGEST, at, rows, step


EXPECTED_QUERY = (
    'index=_internal sourcetype=scheduler savedsearch_name="HomelabAlert - *backup*" '
    'status=success result_count>0 earliest=-24h | stats count AS fires '
    'max(_time) AS last_fire BY savedsearch_name'
)


def test_backup_outcome_query_matches_contract():
    assert DIGEST.BACKUP_OUTCOME_QUERY == EXPECTED_QUERY


def test_backup_outcome_check_runs_once_after_10_utc_per_day():
    assert not DIGEST.backup_outcome_due(at(9), None)
    assert DIGEST.backup_outcome_due(at(10), None)
    assert not DIGEST.backup_outcome_due(
        at(11), {"backup_outcome_checked_date": "2026-07-24"}
    )
    assert DIGEST.backup_outcome_due(at(10, day=25), {"backup_outcome_checked_date": "2026-07-24"})


def test_backup_outcomes_post_name_and_count_then_stay_silent_until_cleared():
    _, state = step(BASE, None, 0)
    when = at(10)

    def clean_rows(moment):
        return rows(BASE, int(moment.timestamp()) - 60)

    outcome = [{
        "savedsearch_name": "HomelabAlert - backup PBS outcome",
        "fires": "2",
        "last_fire": "1784883600",
    }]

    first, state = DIGEST.build_digest(clean_rows(when), when, state, outcome)
    assert "HomelabAlert - backup PBS outcome" in first
    assert "fired 2 time(s)" in first
    assert state["backup_outcome_checked_date"] == "2026-07-24"
    assert "backup-outcome:HomelabAlert - backup PBS outcome" in state["reported_findings"]

    unchanged, state = DIGEST.build_digest(clean_rows(at(11)), at(11), state)
    assert unchanged == DIGEST.SILENT
    assert "backup-outcome:HomelabAlert - backup PBS outcome" in state["reported_findings"]

    cleared, state = DIGEST.build_digest(clean_rows(at(10, day=25)), at(10, day=25), state, [])
    assert cleared == DIGEST.SILENT
    assert "backup-outcome:HomelabAlert - backup PBS outcome" not in state["reported_findings"]

    new_finding, _ = DIGEST.build_digest(clean_rows(at(10, day=26)), at(10, day=26), state, outcome)
    assert "HomelabAlert - backup PBS outcome" in new_finding


def test_a_run_killed_during_the_backup_query_still_marks_today_checked():
    async def tstats(url, token):
        return []

    async def killed(url, token):
        raise KeyboardInterrupt  # stands in for the cron timeout killing the run

    patches = {
        "load_env": lambda path: {"SPLUNK_MCP_URL": "u", "SPLUNK_MCP_TOKEN": "t"},
        "backup_outcome_due": lambda now, state: True,
        "run_tstats": tstats,
        "run_backup_outcomes": killed,
    }
    saved = {name: getattr(DIGEST, name) for name in patches}
    vars(DIGEST).update(patches)
    try:
        DIGEST.main()
        raise AssertionError("the killed query must not be caught")
    except KeyboardInterrupt:
        pass
    finally:
        vars(DIGEST).update(saved)
    today = DIGEST.dt.datetime.now(DIGEST.dt.timezone.utc).strftime("%Y-%m-%d")
    assert DIGEST.load_state()["backup_outcome_checked_date"] == today


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
