"""ntfy titles for the absence-of-success watchdog are keyed on the job or store.

Each title names the job or store alone; counters stay in the body. A recovery
repeats its job's stale title and carries the white_check_mark tag.

These drive the real main() tick with ntfy captured.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from _fabric_watchdog_shared import MINUTE, NOW, WD, job, write_stores

STALE_TITLE = "cron-success-watchdog: job STALE - default/digest"


@pytest.fixture
def tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run one real main() tick and return the ntfy posts it made, in order.

    State lives in a per-test file, so ticks share memory only with each other.
    The clock is pinned to the moment each call names.
    """
    monkeypatch.setattr(WD, "STATE_PATH", str(tmp_path / "cron-success.json"))
    posts: list[dict] = []

    def capture(title, text, priority="default", tags="robot"):
        posts.append({"title": title, "text": text, "priority": priority, "tags": tags})

    monkeypatch.setattr(WD, "ntfy", capture)

    def run_tick(jobs, *, now=NOW, profiles=None, paused=None) -> list[dict]:
        write_stores(jobs, profiles=profiles, paused=paused)
        monkeypatch.setattr(WD, "time", SimpleNamespace(time=lambda: now))
        posts.clear()
        assert WD.main() == 0
        return list(posts)

    return run_tick


def _without_slot_waits(posts: list[dict]) -> list[dict]:
    """Drops slot-wait posts, which a stalled job's already-past slot also produces."""
    return [p for p in posts if p["tags"] != "hourglass_flowing_sand"]


def _baseline_then_stall(tick, cadence_min: int = 15) -> list[dict]:
    """Baseline, then one stall: the job last succeeded at NOW - 5m."""
    fleet = [job("digest", cadence_min=cadence_min, last_ok_ago_min=5)]
    assert tick(fleet) == []
    return _without_slot_waits(tick(fleet, now=NOW + 175 * MINUTE))


def test_a_stale_job_is_titled_by_its_job_and_its_counters_stay_in_the_body(tick) -> None:
    posts = _baseline_then_stall(tick)
    assert [p["title"] for p in posts] == [STALE_TITLE], "the title is the job alone"
    assert posts[0]["tags"] == "rotating_light"
    assert "since last success" in posts[0]["text"], "the counters belong in the body"


def test_two_stale_jobs_get_two_distinct_titles(tick) -> None:
    fleet = [
        job("digest", cadence_min=15, last_ok_ago_min=5),
        job("audit", cadence_min=15, last_ok_ago_min=5),
    ]
    tick(fleet)
    posts = _without_slot_waits(tick(fleet, now=NOW + 175 * MINUTE))
    assert sorted(p["title"] for p in posts) == [
        "cron-success-watchdog: job STALE - default/audit",
        STALE_TITLE,
    ]


def test_a_recovery_repeats_its_jobs_stale_title_and_carries_white_check_mark(tick) -> None:
    stale = _baseline_then_stall(tick)
    recovered = tick([job("digest", cadence_min=15, last_ok_ago_min=-200)], now=NOW + 205 * MINUTE)
    assert len(recovered) == 1, recovered
    assert recovered[0]["title"] == stale[0]["title"] == STALE_TITLE
    assert "white_check_mark" in recovered[0]["tags"].split(",")
    assert recovered[0]["priority"] == "default"
    assert recovered[0]["text"].startswith("default/digest (")


def test_a_recovery_is_titled_by_its_own_job_not_a_sibling(tick) -> None:
    fleet = [
        job("digest", cadence_min=15, last_ok_ago_min=5),
        job("audit", cadence_min=15, last_ok_ago_min=5),
    ]
    tick(fleet)
    tick(fleet, now=NOW + 175 * MINUTE)
    posts = tick(
        [
            job("digest", cadence_min=15, last_ok_ago_min=-200),
            job("audit", cadence_min=15, last_ok_ago_min=5),
        ],
        now=NOW + 205 * MINUTE,
    )
    assert [p["title"] for p in posts] == [STALE_TITLE], "only digest recovered"
    assert "white_check_mark" in posts[0]["tags"].split(",")


def test_a_flapping_job_posts_its_mute_under_its_own_stale_title(tick) -> None:
    """A flapping job's mute line posts under the job's stale title."""
    tick([job("digest", cadence_min=15, last_ok_ago_min=1)])
    posts: list[dict] = []
    t = NOW
    for _ in range(3):
        t += 100 * MINUTE
        posts += _without_slot_waits(
            tick([job("digest", cadence_min=15, last_ok_ago_min=1)], now=t)
        )
        t += 5 * MINUTE
        ran_a_minute_ago = -((t - NOW) / MINUTE) + 1
        posts += _without_slot_waits(
            tick([job("digest", cadence_min=15, last_ok_ago_min=ran_a_minute_ago)], now=t)
        )
    assert {p["title"] for p in posts} == {STALE_TITLE}, posts
    assert [p for p in posts if "FLAPPING" in p["text"]], "the mute itself must post"


def test_a_slot_wait_is_titled_by_its_job(tick) -> None:
    fleet = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    tick(fleet)
    posts = tick(fleet, now=NOW + 26 * MINUTE)
    assert [p["title"] for p in posts] == ["cron-success-watchdog: slot wait - default/digest"]
    assert posts[0]["tags"] == "hourglass_flowing_sand"


def test_a_store_paused_too_long_is_titled_by_its_store(tick) -> None:
    profiles = {"homelab-admin": []}
    tick([], profiles=profiles, paused=["homelab-admin"])
    posts = tick([], now=NOW + 245 * MINUTE, profiles=profiles, paused=["homelab-admin"])
    assert [p["title"] for p in posts] == [
        "cron-success-watchdog: store paused too long - homelab-admin"
    ]


def test_a_mass_stall_stays_one_fleet_ticket_with_the_fleet_title(tick) -> None:
    fleet = [job(f"j{i}", cadence_min=15, last_ok_ago_min=5) for i in range(WD.FLEET_ROLLUP_AT)]
    tick(fleet)
    posts = _without_slot_waits(tick(fleet, now=NOW + 175 * MINUTE))
    assert [p["title"] for p in posts] == ["cron-success-watchdog: fleet STALE"]
    assert posts[0]["priority"] == "high"


def test_every_title_encodes_as_a_latin1_header_value(tick) -> None:
    """Every title is encodable as a latin-1 HTTP header value."""
    posts = _baseline_then_stall(tick)
    posts += tick([job("digest", cadence_min=15, last_ok_ago_min=-200)], now=NOW + 205 * MINUTE)
    assert posts, "premise: the stall and the recovery both posted"
    for post in posts:
        post["title"].encode("latin-1")
