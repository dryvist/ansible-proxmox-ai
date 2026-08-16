"""Self-check for fabric-status.py.j2: renders the deployed template with
fixed test values, stubs probe/gateway/Slack-post, and proves the contract
that replaced the agentic homelab-ai-fabric-status job.

Runs bare (`python3 tests/hermes_agent/test_fabric_status_script.py`) or
under pytest, matching the other script self-checks in this directory.
"""
from __future__ import annotations

import contextlib
import io
import os

from _fabric_status_shared import EXPECTED_URLS, load_fabric_status_module

FS = load_fabric_status_module()

# hermes-env.j2 always writes SLACK_BOT_TOKEN for a deployed guest -- the
# no-token fallback branch (fabric-status.py.j2's ENV_PATH read) is a real
# but separate edge case, not the one these tests exercise.
os.makedirs(os.path.dirname(FS.ENV_PATH), exist_ok=True)
with open(FS.ENV_PATH, "w") as _f:
    _f.write("SLACK_BOT_TOKEN=xoxb-test\n")


def _run(*, healthy: bool):
    """One simulated cron invocation. Returns (rc, stdout, slack_posts, probed_urls)."""
    calls: list[str] = []

    def probe(url):
        calls.append(url)
        return (True, "HTTP 200") if healthy else (False, "probe failed (TimeoutError)")

    # setattr, not FS.probe = ...: FS is a dynamically exec'd ModuleType, so a
    # static checker cannot see these attributes on it either way -- setattr
    # says that plainly instead of tripping reportAttributeAccessIssue.
    setattr(FS, "probe", probe)
    setattr(FS, "gateway_process_ok", lambda: healthy)
    posts: list[tuple[str, str]] = []
    setattr(FS, "post_to_slack", lambda token, channel, text: posts.append((channel, text)))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = FS.main()
    return rc, buf.getvalue(), posts, calls


def _reset_state() -> None:
    if os.path.exists(FS.STATE_PATH):
        os.remove(FS.STATE_PATH)


def test_probes_exactly_the_pinned_endpoints_never_a_guess() -> None:
    """The one incident this script exists to prevent: a model inventing
    endpoints (localhost:8000/:8001, an unresolvable domain) and posting a
    false total-outage alarm off the 000s those guesses returned. Pinning the
    exact URL set here fails the moment a future edit drifts back to guessing."""
    _reset_state()
    _, _, _, calls = _run(healthy=True)
    assert set(calls) == EXPECTED_URLS, f"probed {calls}, expected exactly {EXPECTED_URLS}"


def test_healthy_branch_posts_terse_line_to_noise_and_suppresses_issues() -> None:
    _reset_state()
    rc, out, posts, _ = _run(healthy=True)
    assert rc == 0
    assert len(posts) == 1, f"expected exactly 1 Slack post on the healthy branch, got {posts}"
    channel, text = posts[0]
    assert channel == "C_NOISE", f"healthy branch must post to the noise channel, posted to {channel}"
    assert "All systems operational" in text
    assert out.strip() == "[SILENT]", "healthy branch must print bare [SILENT] to suppress issues delivery"


def test_healthy_and_unchanged_does_not_repost() -> None:
    _reset_state()
    _run(healthy=True)  # seed state
    rc, out, posts, _ = _run(healthy=True)
    assert rc == 0
    assert posts == [], f"unchanged-and-recent healthy run must not repost, got {posts}"
    assert out.strip() == "[SILENT]"


def test_unhealthy_branch_reports_in_full_and_never_posts_to_noise() -> None:
    _reset_state()
    rc, out, posts, _ = _run(healthy=False)
    assert rc == 0
    assert posts == [], f"unhealthy branch must never post to the noise channel, got {posts}"
    assert "NOT all checks healthy" in out
    assert "probe failed" in out
    assert "[SILENT]" not in out, "unhealthy branch must not suppress its own issues-channel delivery"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
