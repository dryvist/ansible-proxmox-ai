"""The double-release regression guard, split out of
test_fast_subagent_lock_behavioral.py to stay under the repo's per-file
token budget. Reuses that file's helpers (same fixture context, same real-
Redis probe and skip/fail switch) rather than duplicating them — see that
file's own docstring for the Redis/stub rationale this inherits.

WHY A DIFFERENT-ALIAS WITNESS IS NOT ENOUGH. An earlier version of this
guard used a second alias ("bob") to prove a second release call was a
no-op. That passed even with `_maybe_release`'s `metadata.pop(...)`
reverted to `metadata.get(...)`, because the release scripts' own
holder-CAS (`GET(LOCK_KEY) == caller_id`) already refuses to act once a
DIFFERENT caller holds the lock — a real, independent guarantee, kept below
as test_release_never_steals_another_aliases_hold, but not a test of the
pop fix. The pop fix protects a narrower, INTRA-alias case: the SAME
alias's own second, still in-flight request. Only
test_mid_stream_error_releases_exactly_once_not_twice below exercises
that — see its own docstring, and the mutation-check results recorded in
this PR's history for direct proof it fails without the fix.
"""

from __future__ import annotations

import pytest

from test_fast_subagent_lock_behavioral import (  # noqa: F401 - re-exercises the same skip/fail switch
    _REDIS_ADDR,
    _REDIS_SKIP_REASON,
    _FakeKey,
    _cleanup,
    _load_module,
    _new_client,
    _run,
)

pytestmark = pytest.mark.skipif(_REDIS_ADDR is None, reason=_REDIS_SKIP_REASON or "redis unavailable")


def test_mid_stream_error_releases_exactly_once_not_twice():
    # request 1 (alice) acquires -> counter 1. request 2 (alice, a second,
    # CONCURRENT role call, same alias) refreshes -> counter 2. request 1
    # errors mid-stream: the iterator hook's `finally` is release #1 ->
    # counter 1, lock still "alice" (request 2 still in flight). litellm's
    # own except-block then calls the failure hook on request 1's SAME
    # request_data -> release #2 attempt. Popping the participates flag
    # (not merely getting it) is what makes release #2 a no-op; without it,
    # release #2 decrements to 0 and DELETEs the lock out from under
    # request 2, which is still using it — the actual double-release bug.
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            alice = _FakeKey("alice")

            request1 = {"model": "fixture-role-fast"}
            await lock.async_pre_call_hook(alice, None, request1, "acompletion")
            request2 = {"model": "fixture-role-subagent"}
            await lock.async_pre_call_hook(alice, None, request2, "acompletion")
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) == "2"

            async def _raising_chunks():
                yield "a"
                raise RuntimeError("upstream error mid-stream")

            with pytest.raises(RuntimeError):
                async for _ in lock.async_post_call_streaming_iterator_hook(alice, _raising_chunks(), request1):
                    pass
            # release #1 (the finally block): request 1's own share only.
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) == "1"
            assert await client.get(ns["LOCK_KEY"]) == "alice", "request 2 is still in flight"

            # release #2: litellm's own except-block calling the failure
            # hook on request 1's SAME (already-released) request_data.
            await lock.async_post_call_failure_hook(request1, RuntimeError("upstream error mid-stream"), alice)
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) == "1", "the second release must be a no-op"
            assert await client.get(ns["LOCK_KEY"]) == "alice", "request 2's hold must survive request 1's double-release"

            # request 2 finishes normally: only now should the hold clear.
            await lock.async_post_call_success_hook(request2, alice, response=object())
            assert await client.get(ns["LOCK_KEY"]) is None
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) is None
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


def test_release_never_steals_another_aliases_hold():
    # A DIFFERENT, real guarantee — the release scripts' holder-CAS refuses
    # a stale release once a different caller has since taken the lock —
    # kept for its own sake. Does NOT exercise the pop fix: see the module
    # docstring for why.
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            alice = _FakeKey("alice")
            data = {"model": "fixture-role-fast"}
            await lock.async_pre_call_hook(alice, None, data, "acompletion")
            await lock.async_post_call_success_hook(data, alice, response=object())
            assert await client.get(ns["LOCK_KEY"]) is None

            bob = _FakeKey("bob")
            bob_data = {"model": "fixture-role-fast"}
            await lock.async_pre_call_hook(bob, None, bob_data, "acompletion")
            assert await client.get(ns["LOCK_KEY"]) == "bob"

            # A stale, late release call carrying alice's already-used data.
            await lock.async_post_call_failure_hook(data, RuntimeError("late"), alice)
            assert await client.get(ns["LOCK_KEY"]) == "bob"
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) == "1"
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
