"""fabric_watchdog debounce, executed.

fabric-watchdog.sh is rendered with the role defaults and one test target, then
run once per probe tick in a temporary state dir. A stub curl answers each probe
from a queue the test fills and records every ntfy publish, so each assertion is
about what the script sends on which tick. Thresholds come from
defaults/main.yml and are not re-typed here.

Lives under tests/hermes_agent/ because fabric_watchdog runs on the Hermes guest.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

import pytest
from _fabric_watchdog_shared import WD as success_watchdog
from _role_files import template_text
from _shell_harness_shared import (
    ROLES,
    defaults_of,
    headers_of,
    install_stubs,
    queue_probe_codes,
    records,
    render,
    run_script,
)

ROLE = ROLES / "fabric_watchdog"
DEFAULTS = defaults_of("fabric_watchdog")
DOWN_AFTER = DEFAULTS["fabric_watchdog_down_after"]
UP_AFTER = DEFAULTS["fabric_watchdog_up_after"]
NTFY_URL = "https://ntfy.test.invalid/ai"
TARGET_URL = "https://probe.test.invalid/health"
DOWN_TITLE = "fabric watchdog: probe DOWN"
RECOVERED_TITLE = "fabric watchdog: probe recovered"


class Watchdog:
    """One rendered fabric-watchdog.sh, its stubs, and its temporary state dir."""

    def __init__(self, root: Path) -> None:
        self.env = install_stubs(root)
        self.state_dir = root / "state"
        self.script = root / "fabric-watchdog.sh"
        context = {
            **DEFAULTS,
            "ansible_managed": "test render",
            "fabric_watchdog_state_dir": str(self.state_dir),
            "fabric_watchdog_ntfy_url": NTFY_URL,
            "fabric_watchdog_targets": [{"name": "probe", "url": TARGET_URL, "ok_codes": "200"}],
        }
        self.script.write_text(render(template_text(ROLE, "fabric-watchdog.sh.j2"), context))
        self.script.chmod(0o755)

    def drive(self, codes: list[str]) -> list[int]:
        """Run one tick per probe status; return how many publishes each tick sent."""
        sent_per_tick = []
        for code in codes:
            queue_probe_codes(self.env, [code])
            before = len(self.posts())
            proc = run_script(self.script, [], self.env)
            assert proc.returncode == 0, proc.stderr
            sent_per_tick.append(len(self.posts()) - before)
        return sent_per_tick

    def posts(self) -> list[dict]:
        return records(self.env, "posts.jsonl")

    def committed(self) -> str | None:
        """The edge the script has committed, or None before any edge."""
        path = self.state_dir / "probe.state"
        return path.read_text().strip() if path.exists() else None


@pytest.fixture
def watchdog(tmp_path: Path) -> Watchdog:
    return Watchdog(tmp_path)


def test_n_consecutive_downs_send_exactly_one_down_alert(watchdog: Watchdog) -> None:
    assert watchdog.drive(["503"] * (DOWN_AFTER - 1)) == [0] * (DOWN_AFTER - 1)
    assert watchdog.committed() is None, "one short of the threshold must commit nothing"

    assert watchdog.drive(["503"]) == [1]
    assert watchdog.committed() == "down"
    assert watchdog.drive(["503"] * 3) == [0, 0, 0], "a sustained outage must not re-alert"

    posts = watchdog.posts()
    assert len(posts) == 1
    headers = headers_of(posts[0])
    assert headers["Title"] == DOWN_TITLE
    assert headers["Priority"] == "high"
    assert posts[0]["url"] == NTFY_URL, "every alert goes to the ntfy hub"
    assert TARGET_URL in posts[0]["body"] and "503" in posts[0]["body"]
    assert {probe["url"] for probe in records(watchdog.env, "probes.jsonl")} == {TARGET_URL}


def test_a_flapping_endpoint_below_the_threshold_sends_nothing(watchdog: Watchdog) -> None:
    """Runs shorter than the threshold never commit, in either direction.

    From up: DOWN_AFTER-1 downs then an up, repeated. Once a DOWN is committed,
    UP_AFTER-1 ups then a down, repeated, must not be read as a recovery.
    """
    up_flap = ["503"] * (DOWN_AFTER - 1) + ["200"]
    assert watchdog.drive(up_flap * 4) == [0] * (len(up_flap) * 4)
    assert watchdog.committed() is None

    assert watchdog.drive(["503"] * DOWN_AFTER) == [0] * (DOWN_AFTER - 1) + [1]
    down_flap = ["200"] * (UP_AFTER - 1) + ["503"]
    assert watchdog.drive(down_flap * 4) == [0] * (len(down_flap) * 4)
    assert len(watchdog.posts()) == 1, "only the committed DOWN may have been sent"


def test_m_consecutive_ups_after_a_down_send_exactly_one_recovery(watchdog: Watchdog) -> None:
    assert watchdog.drive(["503"] * DOWN_AFTER) == [0] * (DOWN_AFTER - 1) + [1]

    assert watchdog.drive(["200"] * (UP_AFTER - 1)) == [0] * (UP_AFTER - 1)
    assert watchdog.drive(["200"]) == [1]
    assert watchdog.committed() == "up"
    assert watchdog.drive(["200"] * 3) == [0, 0, 0]

    recovered = watchdog.posts()[-1]
    headers = headers_of(recovered)
    assert headers["Title"] == RECOVERED_TITLE
    assert headers["Priority"] == "default"
    assert recovered["url"] == NTFY_URL


def test_steady_state_sends_nothing(watchdog: Watchdog) -> None:
    assert watchdog.drive(["200"] * 10) == [0] * 10
    assert watchdog.committed() is None
    assert watchdog.posts() == []


def test_the_success_watchdog_publishes_only_to_the_ntfy_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """The absence-of-success half of this role publishes through the same hub."""
    requested: list[str] = []

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def read(self) -> bytes:
            return b""

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        requested.append(request.full_url)
        return _Response()

    monkeypatch.setattr(success_watchdog, "NTFY_URL", NTFY_URL)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    success_watchdog.ntfy("cron success watchdog: test", "probe")
    assert requested == [NTFY_URL]


def test_debounce_is_never_reduced_to_the_undebounced_behaviour() -> None:
    """down_after or up_after of 1 restores the noisy original. Guard the values,
    so a defaults tweak cannot undo the fix while every behaviour test stays green.
    """
    assert DOWN_AFTER >= 2, "down_after=1 is the un-debounced behaviour this role was fixed for"
    assert UP_AFTER >= 2, "up_after=1 lets a single lucky probe declare recovery"


def test_debounce_still_pages_within_a_useful_window() -> None:
    """The role exists for minutes-level detection, so the debounce must not push
    detection into tens of minutes. Silence bought by an unusable delay is not a fix.
    """
    match = re.fullmatch(r"(\d+)min", DEFAULTS["fabric_watchdog_interval"])
    assert match is not None, "fabric_watchdog_interval is no longer a whole number of minutes"
    minutes_to_page = int(match.group(1)) * DOWN_AFTER
    assert minutes_to_page <= 10, (
        f"{minutes_to_page} min to detect a fabric outage is too slow for a "
        "minutes-level watchdog; lower down_after or the interval"
    )
