"""A flapping job collapses into ONE mute post; the mute lifts once it is stable.

2026-09-27: a retired burst cron produced seven watchdog posts in five hours
(alert, recover, alert, ...) for zero operator action. The mute says the JOB is
wrong; the watchdog keeps its own clock running underneath so a job that stops
flapping and simply dies still pages once the mute expires.

Runs under pytest from the repo root.
"""

from _fabric_watchdog_shared import MINUTE, NOW, job, run, settle


def _healthy(t):
    # A job whose last success landed one minute before `t`.
    return [job("burst", cadence_min=15, last_ok_ago_min=-((t - NOW) / MINUTE) + 1)]


def _dead(t):
    # The same job with no success since the baseline tick (NOW - 1m).
    return [job("burst", cadence_min=15, last_ok_ago_min=1)]


def test_three_cycles_inside_the_window_post_one_mute_then_nothing() -> None:
    state = settle([job("burst", cadence_min=15, last_ok_ago_min=1)])
    posts = []
    t = NOW
    for _ in range(3):
        t += 100 * MINUTE  # > 3x15m threshold (floor 30m): overdue
        stale, _, state, _ = run(_dead(t), state, now=t)
        posts.extend(stale)
        t += 5 * MINUTE
        _, recovered, state, _ = run(_healthy(t), state, now=t)
        posts.extend(recovered)
    flapping = [p for p in posts if "FLAPPING" in p]
    assert len(flapping) == 1, posts
    assert posts[-1] is flapping[0], "the third alert is the mute, and its recovery is not posted"

    t += 100 * MINUTE
    stale, _, state, _ = run(_dead(t), state, now=t)
    assert stale == [], "muted job must not page again while muted"


def test_the_mute_lifts_after_the_quiet_period_and_the_next_stall_pages_normally() -> None:
    state = settle([job("burst", cadence_min=15, last_ok_ago_min=1)])
    t = NOW
    for _ in range(3):
        t += 100 * MINUTE
        _, _, state, _ = run(_dead(t), state, now=t)
        t += 5 * MINUTE
        _, _, state, _ = run(_healthy(t), state, now=t)
    # Stable well past FLAP_QUIET_MINUTES (120): two healthy ticks 130m apart.
    t += 130 * MINUTE
    _, _, state, _ = run(_healthy(t), state, now=t)
    t += 100 * MINUTE
    stale, _, _, _ = run(_dead(t), state, now=t)
    assert len(stale) == 1 and "FLAPPING" not in stale[0], stale
