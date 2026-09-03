"""Self-check for the per-tick dispatch decision log patches
(patches_dispatch_tick_log.yml): the two replace tasks against the pinned
upstream text, and the helper's line against a DispatchResult-shaped object.

Runs bare or under pytest.
"""
import runpy
import tempfile
import types
from pathlib import Path

from _pinned_sources import PINNED_DISPATCH_TICK_SOURCE
from _role_files import role_tasks
from conftest import _apply_runtime_patch

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"


def _helper():
    block = next(
        t["ansible.builtin.blockinfile"]["block"]
        for t in role_tasks(ROLE, "patches_dispatch_tick_log.yml")
        if t.get("name") == "Add the dispatch tick line helper"
    )
    path = Path(tempfile.mkdtemp(prefix="dispatch-tick-selfcheck-")) / "helper.py"
    path.write_text(block)
    return runpy.run_path(str(path), run_name="helper")["_dispatch_tick_line"]


def _result(**fields):
    base = dict(spawned=[], reclaimed=0, promoted=0, crashed=[], timed_out=[], stale=[],
                auto_blocked=[], rate_limited=[], skipped_unassigned=[],
                skipped_nonspawnable=[], skipped_per_profile_capped=[], respawn_guarded=[],
                reconciled_orphans=[], skipped_locked=False, memory_pressure=None)
    base.update(fields)
    return types.SimpleNamespace(**base)


def test_an_idle_tick_logs_nothing():
    assert _helper()("main", _result()) == ""


def test_every_non_zero_outcome_is_named_with_its_count():
    line = _helper()("main", _result(
        spawned=[("t1", "p", "/w")], skipped_per_profile_capped=[("t2", "p", 1), ("t3", "p", 1)],
        respawn_guarded=[("t4", "recent_success"), ("t5", "blocker_auth")],
        skipped_locked=True, memory_pressure="elevated"))
    assert line.startswith("kanban dispatcher tick [main]: ")
    for part in ("spawned=1", "skipped_per_profile_capped=2", "respawn_guarded=2",
                 "skipped_locked=1", "memory_pressure=elevated",
                 "respawn_reasons=blocker_auth,recent_success"):
        assert part in line, part
    assert "reclaimed=" not in line, "zero outcomes stay out of the line"


def test_both_replace_patches_anchor_once_and_the_result_is_wired():
    patched = _apply_runtime_patch(
        "Log ready work that nothing was spawned for",
        _apply_runtime_patch("Log every dispatch tick outcome, not only spawns",
                             PINNED_DISPATCH_TICK_SOURCE),
    )
    assert "_tick_line = _dispatch_tick_line(slug, res)" in patched
    assert 'any_spawned = True' in patched
    assert "nothing spawned " in patched and "bad_ticks % 10 == 0" in patched
    # Idempotent: neither anchor survives its own rewrite.
    for name in ("Log every dispatch tick outcome, not only spawns",
                 "Log ready work that nothing was spawned for"):
        try:
            _apply_runtime_patch(name, patched)
        except AssertionError:
            continue
        raise AssertionError(f"{name} still matches after being applied")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
