"""Behavioral tests for the studio gate's in-flight-counted lock, against a
REAL Redis connection. Split out of test_fast_subagent_lock_behavioral.py
(same Redis probe/skip/fail switch, same stub-module rationale) to stay
under the repo's per-file token budget — see that file's own docstring.

WHY THESE EXIST. test_fast_subagent_lock_studio.py only text-asserts over
the rendered template's source; it cannot prove the studio gate actually
behaves like "in-flight work only, never a session" against a real store.
These three scenarios are exactly the ones the studio-never-holds-a-session
fix depends on:

  1. Caller A completes -> caller B (same or different alias) is served
     LOCALLY (data["model"] unchanged), never redirected, because nothing
     is in flight any more.
  2. Caller A is still in flight when caller B arrives -> B is redirected
     to STUDIO_DIRECT_OVERFLOW[name], the SAME outcome a genuinely busy
     backend should produce.
  3. Caller A's own completion releases the hold WITHOUT any release
     header — unlike the 4080's direct callers, the studio has no
     header-gated session release path at all.
"""

from __future__ import annotations

import pytest

from test_fast_subagent_lock_behavioral import (  # noqa: F401 - reuses the same skip/fail switch
    _REDIS_ADDR,
    _REDIS_SKIP_REASON,
    _FakeKey,
    _cleanup,
    _load_module,
    _new_client,
    _run,
)

pytestmark = pytest.mark.skipif(_REDIS_ADDR is None, reason=_REDIS_SKIP_REASON or "redis unavailable")


def test_studio_caller_completes_then_a_second_caller_is_served_locally():
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            alice = _FakeKey("alice")
            bob = _FakeKey("bob")

            data_a = {"model": "fixture-studio-a"}
            await lock.async_pre_call_hook(alice, None, data_a, "acompletion")
            assert data_a["model"] == "fixture-studio-a", "must proceed, not redirect, while uncontended"
            await lock.async_post_call_success_hook(data_a, alice, response=object())
            assert await client.get(ns["STUDIO_LOCK_KEY"]) is None, "A's completion must release the studio hold"

            data_b = {"model": "fixture-studio-a"}
            await lock.async_pre_call_hook(bob, None, data_b, "acompletion")
            assert data_b["model"] == "fixture-studio-a", "B must be served locally once A is no longer in flight"
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


def test_studio_caller_in_flight_redirects_a_second_caller():
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            alice = _FakeKey("alice")
            bob = _FakeKey("bob")

            data_a = {"model": "fixture-studio-a"}
            await lock.async_pre_call_hook(alice, None, data_a, "acompletion")
            assert data_a["model"] == "fixture-studio-a"

            data_b = {"model": "fixture-studio-a"}
            await lock.async_pre_call_hook(bob, None, data_b, "acompletion")
            assert data_b["model"] == ns["STUDIO_DIRECT_OVERFLOW"]["fixture-studio-a"], (
                "a genuinely in-flight caller must redirect the second one, never raise or queue silently"
            )
            assert ns["_STUDIO_LOCK_PARTICIPATES_KEY"] not in data_b.get("metadata", {}), (
                "a redirected attempt must never be mistaken for a hold"
            )
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


def test_studio_release_needs_no_header_unlike_the_4080s_direct_path():
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            alice = _FakeKey("alice")
            data = {"model": "fixture-studio-a"}
            await lock.async_pre_call_hook(alice, None, data, "acompletion")
            assert await client.get(ns["STUDIO_LOCK_KEY"]) == "alice"

            # No X-Subagent-Release header anywhere in `data` — unlike the
            # 4080's direct path, the studio must release unconditionally.
            await lock.async_post_call_success_hook(data, alice, response=object())
            assert await client.get(ns["STUDIO_LOCK_KEY"]) is None, "the studio hold must not need a release header"
            assert await client.get(ns["_STUDIO_ROLE_INFLIGHT_KEY"]) is None
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
