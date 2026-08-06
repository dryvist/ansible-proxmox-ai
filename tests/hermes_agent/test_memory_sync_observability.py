"""_sync_external_memory_for_turn() (run_agent.py) is the only place that
queues the NEXT turn's prefetch — it runs at end-of-turn, gated behind three
silent early returns plus a bare ``except Exception: pass``, none of which
logged anything. prefetch_all() (turn_context.py), by contrast, runs
unconditionally at the start of every turn. A turn whose predecessor hit any
of the four silent exits leaves nothing queued, and the next turn's prefetch
reports empty with zero trace of why.

This does not change what any of the four exits DO (same interrupted-turn
skip, same best-effort swallow) — it only makes each one observable. These
tests apply the role's own regex patches to a pinned upstream-shaped fixture
(never a hand-written "expected" string, so the test can't assert against
itself) and confirm: each gate logs a distinct message, none of the four
leak message/user/response text, and none of the four change what the
function returns or raises.

Runs bare (``python3 tests/hermes_agent/test_memory_sync_observability.py``)
or under pytest.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"

# Verbatim upstream shape of run_agent.py's _sync_external_memory_for_turn —
# indentation included, since the role's regexes capture and reuse it (same
# drift protection as every other PINNED_*_SOURCE fixture in this repo: if
# upstream reindents or rewords this method, these patches should stop
# matching and this test should go red, not silently patch nothing).
PINNED_SYNC_EXTERNAL_MEMORY_SOURCE = '''\
class _Agent:
    def _sync_external_memory_for_turn(
        self,
        *,
        original_user_message: Any,
        final_response: Any,
        interrupted: bool,
        messages: list | None = None,
    ) -> None:
        if interrupted:
            return
        if not (self._memory_manager and final_response and original_user_message):
            return
        user_text = _summarize_user_message_for_log(original_user_message, sep="\\n")
        response_text = _summarize_user_message_for_log(final_response, sep="\\n")
        if not (user_text and response_text):
            return
        try:
            sync_kwargs = {"session_id": self.session_id or ""}
            if messages is not None:
                sync_kwargs["messages"] = messages
            self._memory_manager.sync_all(
                user_text,
                response_text,
                **sync_kwargs,
            )
            self._memory_manager.queue_prefetch_all(
                user_text,
                session_id=self.session_id or "",
            )
        except Exception:
            pass
'''


def _task(name: str) -> dict[str, Any]:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "main.yml").read_text())
    return next(item for item in tasks if item.get("name") == name)


def _apply_runtime_patch(name: str, source: str) -> str:
    config = _task(name)["ansible.builtin.replace"]
    patched, count = re.subn(config["regexp"], config["replace"], source, flags=re.MULTILINE)
    assert count == 1, f"{name!r} matched {count} times, expected 1"
    return patched


# Derived by running the role's own four patches over the pinned upstream
# source, never hand-written — a hand-copied "expected" string can drift from
# what the role actually produces and would assert against itself.
PATCHED_SOURCE = PINNED_SYNC_EXTERNAL_MEMORY_SOURCE
for _task_name in (
    'Patch _sync_external_memory_for_turn to log its "interrupted" skip',
    "Patch _sync_external_memory_for_turn to log its missing-input skip",
    "Patch _sync_external_memory_for_turn to log its empty-flatten skip",
    "Patch _sync_external_memory_for_turn to log its swallowed exception",
):
    PATCHED_SOURCE = _apply_runtime_patch(_task_name, PATCHED_SOURCE)


class _FakeLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple] = []

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warnings.append(args)


class _FakeMemoryManager:
    def __init__(self, *, sync_raises: Exception | None = None) -> None:
        self.sync_raises = sync_raises
        self.synced = False
        self.queued = False

    def sync_all(self, *args: Any, **kwargs: Any) -> None:
        if self.sync_raises:
            raise self.sync_raises
        self.synced = True

    def queue_prefetch_all(self, *args: Any, **kwargs: Any) -> None:
        self.queued = True


def _build(*, memory_manager: _FakeMemoryManager | None, session_id: str = "s1"):
    """Exec the patched source into a fresh namespace and return
    (bound_method, agent, logger). Same pattern as the other
    _apply_runtime_patch-based tests in this repo (test_goal_mode_contract.py)
    — the fixture is our own pinned+patched source, not external input."""
    fake_logger = _FakeLogger()
    namespace: dict[str, Any] = {
        "Any": Any,
        "_summarize_user_message_for_log": lambda v, sep="\n": v if isinstance(v, str) else "",
        "logger": fake_logger,
    }
    exec(PATCHED_SOURCE, namespace)
    agent = namespace["_Agent"]()
    agent._memory_manager = memory_manager
    agent.session_id = session_id
    return agent._sync_external_memory_for_turn, agent, fake_logger


def test_each_gate_logs_a_distinct_message_exactly_once() -> None:
    assert PATCHED_SOURCE.count("Hermes external-memory sync/prefetch skipped: turn interrupted") == 1
    assert PATCHED_SOURCE.count("Hermes external-memory sync/prefetch skipped: missing manager=%s") == 1
    assert PATCHED_SOURCE.count("Hermes external-memory sync/prefetch skipped: empty after flatten") == 1
    assert PATCHED_SOURCE.count("Hermes external-memory sync_all/queue_prefetch_all failed: %s") == 1


def test_no_gate_logs_message_or_response_content() -> None:
    """The four new log lines carry booleans/session id/exception text only —
    never the raw user_text/response_text/original_user_message/final_response
    values, which would ship real conversation content into Splunk."""
    warning_calls = re.findall(r"logger\.warning\((?:[^()]|\([^()]*\))*\)", PATCHED_SOURCE)
    assert len(warning_calls) == 4
    for call in warning_calls:
        assert "user_text,\n" not in call
        assert "response_text,\n" not in call
        assert re.search(r"\boriginal_user_message,\n", call) is None
        assert re.search(r"\bfinal_response,\n", call) is None


def test_interrupted_gate_logs_and_still_returns_without_syncing() -> None:
    method, agent, log = _build(memory_manager=_FakeMemoryManager())
    method(original_user_message="hi", final_response="hello", interrupted=True)
    assert len(log.warnings) == 1
    assert log.warnings[0][0] == "Hermes external-memory sync/prefetch skipped: turn interrupted (session=%s)"
    assert agent._memory_manager.synced is False
    assert agent._memory_manager.queued is False


def test_missing_input_gate_logs_which_input_and_still_returns() -> None:
    method, agent, log = _build(memory_manager=_FakeMemoryManager())
    method(original_user_message="hi", final_response=None, interrupted=False)
    assert len(log.warnings) == 1
    args = log.warnings[0]
    assert args[0].startswith("Hermes external-memory sync/prefetch skipped: missing manager=%s")
    # manager=True, final_response=False, original_user_message=True
    assert args[1:4] == (True, False, True)
    assert agent._memory_manager.synced is False


def test_empty_flatten_gate_logs_and_still_returns() -> None:
    method, agent, log = _build(memory_manager=_FakeMemoryManager())
    # A non-string original_user_message flattens to "" per the fake helper,
    # exercising the third gate without touching the first two.
    method(original_user_message=[{"type": "image"}], final_response="hello", interrupted=False)
    assert len(log.warnings) == 1
    assert log.warnings[0][0].startswith("Hermes external-memory sync/prefetch skipped: empty after flatten")
    assert agent._memory_manager.synced is False


def test_swallowed_exception_now_logs_but_still_does_not_propagate() -> None:
    method, agent, log = _build(memory_manager=_FakeMemoryManager(sync_raises=RuntimeError("boom")))
    method(original_user_message="hi", final_response="hello", interrupted=False)  # must not raise
    assert len(log.warnings) == 1
    args = log.warnings[0]
    assert args[0] == "Hermes external-memory sync_all/queue_prefetch_all failed: %s (session=%s)"
    assert isinstance(args[1], RuntimeError)
    assert agent._memory_manager.queued is False  # never reached queue_prefetch_all


def test_happy_path_logs_nothing() -> None:
    method, agent, log = _build(memory_manager=_FakeMemoryManager())
    method(original_user_message="hi", final_response="hello", interrupted=False)
    assert log.warnings == []
    assert agent._memory_manager.synced is True
    assert agent._memory_manager.queued is True


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
