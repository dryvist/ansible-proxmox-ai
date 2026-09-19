"""Proves LLM_ROUTER_REQUIRE_REDIS=1 turns an unreachable Redis into a
collection-time FAILURE for test_fast_subagent_lock_behavioral.py, not a
skip — the property that file's docstring and CI wiring both depend on.

Runs in a subprocess (not an in-process monkeypatch + reimport) because the
target file's own module-level `pytest.fail` fires at IMPORT time: an
in-process re-import would abort THIS test's own collection/run the same
way, rather than letting us observe and assert on the failure. A subprocess
with its own forced, guaranteed-closed port is the only way to see both
"it failed" and "it failed for the right reason" from the outside.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BEHAVIORAL_TEST = Path(__file__).resolve().parent / "test_fast_subagent_lock_behavioral.py"


def test_require_redis_env_var_fails_collection_when_unreachable():
    env = dict(os.environ)
    env["LLM_ROUTER_REQUIRE_REDIS"] = "1"
    env["REDIS_HOST"] = "127.0.0.1"
    # Port 1 is a privileged port nothing in this environment listens on —
    # deterministically unreachable, independent of whatever real Redis the
    # OUTER test run (this file's own parent pytest invocation) may have.
    env["REDIS_PORT"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(BEHAVIORAL_TEST), "-q"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"expected a nonzero exit (failure), got 0:\n{output}"
    assert "LLM_ROUTER_REQUIRE_REDIS=1 but Redis is unavailable" in output, output
    # Anti-vacuity: must not be reported as merely skipped.
    assert "skipped" not in output.lower() or "failed" in output.lower(), output


def test_without_the_var_the_same_unreachable_redis_only_skips():
    # The other half of the asymmetry the module docstring promises: with
    # the var unset (the local-dev default), the identical unreachable
    # Redis must skip, not fail — this file exists to prove the switch
    # works both ways, not just that the fail path fires.
    env = dict(os.environ)
    env.pop("LLM_ROUTER_REQUIRE_REDIS", None)
    env["REDIS_HOST"] = "127.0.0.1"
    env["REDIS_PORT"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(BEHAVIORAL_TEST), "-q", "-rs"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"expected a clean skip (exit 0), got {result.returncode}:\n{output}"
    assert "skipped" in output.lower(), output


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
