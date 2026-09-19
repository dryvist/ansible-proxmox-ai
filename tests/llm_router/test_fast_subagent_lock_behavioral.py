"""Behavioral tests for fast_subagent_lock.py.j2 against a REAL Redis
connection. test_fast_subagent_lock.py only text-asserts over the rendered
template's source — it cannot catch a Lua arity error, a DECR going
negative, or the double-release class of bug (all three found in review by
reading, not by a test). This file execs the real rendered module and drives
its real EVAL calls against a real store, so those classes of bug show up as
an assertion failure here instead of only in a live proxy.

REQUIRES a reachable Redis (or Redis-protocol-compatible) server and the
`redis` package — neither is installed by this repo's minimal pytest job
today (.github/workflows/_llm-router-contract.yml: `ansible pytest pyyaml`).
CI adds both explicitly for this file: `redis` on the pip install line, and
a `redis:` service container on the job — see that workflow.

LLM_ROUTER_REQUIRE_REDIS switches what an unreachable Redis MEANS. Unset
(the local-dev default): every test here SKIPS with a reason (`pytestmark`
below) — a bare "N skipped" is visible, but only `pytest -rs` shows why, so
don't read a plain pass count as proof the lock's real Redis behavior ran.
Set to "1" (CI, see the workflow): the same condition is a collection-time
`pytest.fail` instead — this file reports FAILED, not skipped, so a broken
redis: service or a drifted pip line cannot produce a green job with zero
lock coverage. See test_fast_subagent_lock_behavioral_require_redis.py for
the test proving both halves of this switch.

WHY fastapi/litellm ARE NOT REAL DEPENDENCIES HERE. The rendered module
imports `HTTPException` from fastapi and subclasses `CustomLogger` from
litellm.integrations.custom_logger, but neither is exercised for its
framework behavior anywhere in this file — HTTPException is only ever
raised and caught as a plain Python exception (never passed through
FastAPI's own exception-handling machinery), and CustomLogger is an empty
base this callback never calls into. Installing litellm[proxy] in CI to
satisfy that import would pull in a large, slow, unrelated dependency tree
for zero additional coverage of what this file actually tests: the lock's
own control flow and its real Lua scripts against a real store. So this
file imports the real packages when they happen to be present (a full local
litellm[proxy] dev venv, say) and falls back to minimal same-shaped stubs
otherwise — see _install_stub_modules.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/llm_router/templates/callbacks/fast_subagent_lock.py.j2"

# Same synthetic fixture context as test_fast_subagent_lock.py — kept local
# rather than cross-imported so this file's collection does not depend on
# pytest's import-mode/rootdir resolving a sibling test module by bare name.
DEFAULT_CONTEXT = {
    "ansible_managed": "Managed by Ansible",
    "llm_router_subagent_lock_key": "test:subagent:4080:lock",
    "llm_router_subagent_lock_ttl_seconds": 300,
    "llm_router_subagent_lock_release_header": "x-subagent-release",
    "llm_router_subagent_lock_model_ids": ["fixture-model-a", "fixture-model-b", "fixture-model-c"],
    "llm_router_redis_port": 6379,
    "llm_router_subagent_lock_role_names": ["fixture-role-fast", "fixture-role-subagent"],
    "llm_router_primary_model": "fixture-primary-model",
}


def _comment_filter(text: str) -> str:
    return "\n".join(f"# {line}" if line else "#" for line in str(text).splitlines())


def _render() -> str:
    import jinja2

    env = jinja2.Environment()
    env.filters["to_json"] = lambda v: json.dumps(v)
    env.filters["comment"] = _comment_filter
    return env.from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(**DEFAULT_CONTEXT)


def _install_stub_modules() -> None:
    """Install minimal fastapi/litellm stubs ONLY if the real packages are
    not importable — see the module docstring for why this is enough."""
    try:
        import fastapi  # noqa: F401
    except ImportError:
        stub = types.ModuleType("fastapi")

        class HTTPException(Exception):  # noqa: N818 - matching fastapi's own name
            def __init__(self, status_code, detail=None, headers=None):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail
                self.headers = headers

        stub.HTTPException = HTTPException
        sys.modules["fastapi"] = stub

    try:
        from litellm.integrations.custom_logger import CustomLogger  # noqa: F401
    except ImportError:
        litellm_stub = types.ModuleType("litellm")
        integrations_stub = types.ModuleType("litellm.integrations")
        custom_logger_stub = types.ModuleType("litellm.integrations.custom_logger")

        class CustomLogger:  # noqa: D401 - matching litellm's own empty base
            pass

        custom_logger_stub.CustomLogger = CustomLogger
        integrations_stub.custom_logger = custom_logger_stub
        litellm_stub.integrations = integrations_stub
        sys.modules.setdefault("litellm", litellm_stub)
        sys.modules.setdefault("litellm.integrations", integrations_stub)
        sys.modules.setdefault("litellm.integrations.custom_logger", custom_logger_stub)


def _load_module():
    _install_stub_modules()
    ns: dict = {"__name__": "fast_subagent_lock_under_test"}
    exec(compile(_render(), str(TEMPLATE_PATH), "exec"), ns)  # noqa: S102 - test-only, source is our own template render
    return ns


def _redis_probe():
    try:
        import redis.asyncio as redis_asyncio
    except ImportError:
        return None, "redis package not installed in this environment"
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))

    async def _ping() -> bool:
        client = redis_asyncio.Redis(host=host, port=port, decode_responses=True, socket_connect_timeout=2)
        try:
            return bool(await client.ping())
        except Exception:
            return False
        finally:
            await client.aclose()

    try:
        ok = asyncio.run(_ping())
    except Exception:
        ok = False
    if not ok:
        return None, f"no Redis reachable at {host}:{port}"
    return (host, port), None


_REDIS_ADDR, _REDIS_SKIP_REASON = _redis_probe()
_REQUIRE_REDIS = os.environ.get("LLM_ROUTER_REQUIRE_REDIS", "") == "1"

if _REDIS_ADDR is None and _REQUIRE_REDIS:
    # See the module docstring: fail collection, don't skip, under the var.
    pytest.fail(
        f"LLM_ROUTER_REQUIRE_REDIS=1 but Redis is unavailable: {_REDIS_SKIP_REASON}",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(_REDIS_ADDR is None, reason=_REDIS_SKIP_REASON or "redis unavailable")


class _FakeKey:
    """Minimal stand-in for litellm's UserAPIKeyAuth — this callback only
    ever reads key_alias/token off it (see _caller_id in the template)."""

    def __init__(self, alias: str):
        self.key_alias = alias
        self.token = None


def _new_client(ns):
    import redis.asyncio as redis_asyncio

    host, port = _REDIS_ADDR
    return redis_asyncio.Redis(host=host, port=port, decode_responses=True)


async def _cleanup(client, ns):
    await client.delete(ns["LOCK_KEY"], ns["_ROLE_INFLIGHT_KEY"])


def _run(coro):
    return asyncio.run(coro)


def test_role_acquire_sets_lock_and_counter_and_participates_flag():
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            data = {"model": "fixture-role-fast"}
            result = await lock.async_pre_call_hook(_FakeKey("alice"), None, data, "acompletion")
            assert result is data
            assert await client.get(ns["LOCK_KEY"]) == "alice"
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) == "1"
            assert data["metadata"][ns["_LOCK_PARTICIPATES_KEY"]] is True
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


def test_two_concurrent_role_calls_share_the_hold_first_finish_does_not_evict_second():
    # Case (b) from the module docstring, driven end to end: sibling 1
    # acquires, sibling 2 refreshes (counter -> 2), sibling 1 finishes via
    # the streaming iterator hook and the lock must SURVIVE for sibling 2;
    # only sibling 2 finishing deletes it.
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            key = _FakeKey("alice")

            data1 = {"model": "fixture-role-fast"}
            await lock.async_pre_call_hook(key, None, data1, "acompletion")
            data2 = {"model": "fixture-role-subagent"}
            await lock.async_pre_call_hook(key, None, data2, "acompletion")
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) == "2"

            async def _chunks():
                yield "a"
                yield "b"

            drained1 = [c async for c in lock.async_post_call_streaming_iterator_hook(key, _chunks(), data1)]
            assert drained1 == ["a", "b"]
            assert await client.get(ns["LOCK_KEY"]) == "alice", "sibling 1 finishing must not evict sibling 2"
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) == "1"

            drained2 = [c async for c in lock.async_post_call_streaming_iterator_hook(key, _chunks(), data2)]
            assert drained2 == ["a", "b"]
            assert await client.get(ns["LOCK_KEY"]) is None
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) is None
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


def test_role_call_refreshing_a_direct_holders_lock_does_not_steal_it():
    # Case (a) from the module docstring: a direct caller's own hold,
    # refreshed by a role call from the SAME alias, must not be torn down
    # when that role call completes.
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            key = _FakeKey("alice")

            direct_data = {"model": "fixture-model-a"}
            await lock.async_pre_call_hook(key, None, direct_data, "acompletion")
            assert await client.get(ns["LOCK_KEY"]) == "alice"
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) is None

            role_data = {"model": "fixture-role-fast"}
            await lock.async_pre_call_hook(key, None, role_data, "acompletion")
            assert role_data["model"] == "fixture-role-fast", "must proceed, not redirect (outcome 1)"
            assert ns["_LOCK_PARTICIPATES_KEY"] not in role_data.get("metadata", {})

            await lock.async_post_call_success_hook(role_data, key, response=object())
            assert await client.get(ns["LOCK_KEY"]) == "alice", "the role call must not release the direct hold"
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


def test_release_refuses_to_delete_when_counter_already_expired():
    # The EXISTS guard: simulate the counter vanishing (TTL expiry, or a
    # long role stream outlasting a direct caller's EXPIRE-only refreshes)
    # while the lock itself is still present. Release must be a no-op, not
    # a DECR-to-negative-then-DEL.
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            key = _FakeKey("alice")
            data = {"model": "fixture-role-fast"}
            await lock.async_pre_call_hook(key, None, data, "acompletion")
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) == "1"

            await client.delete(ns["_ROLE_INFLIGHT_KEY"])  # simulate expiry

            await lock.async_post_call_success_hook(data, key, response=object())
            assert await client.get(ns["LOCK_KEY"]) == "alice", "lock must survive a release with no counter"
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


def test_mid_stream_error_releases_exactly_once_not_twice():
    # Regression guard for the double-release bug: litellm's own generator
    # wrapper reaches BOTH the streaming iterator hook's `finally` and,
    # afterwards, async_post_call_failure_hook on the SAME request_data
    # dict. A second, unguarded release would steal a share belonging to an
    # unrelated caller (alias B, acquiring between the two calls) rather
    # than merely no-op.
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            alice = _FakeKey("alice")
            data = {"model": "fixture-role-fast"}
            await lock.async_pre_call_hook(alice, None, data, "acompletion")
            assert await client.get(ns["LOCK_KEY"]) == "alice"

            async def _raising_chunks():
                yield "a"
                raise RuntimeError("upstream error mid-stream")

            with pytest.raises(RuntimeError):
                async for _ in lock.async_post_call_streaming_iterator_hook(alice, _raising_chunks(), data):
                    pass
            # release #1 (the finally block) must have fired: lock is free.
            assert await client.get(ns["LOCK_KEY"]) is None

            bob = _FakeKey("bob")
            bob_data = {"model": "fixture-role-fast"}
            await lock.async_pre_call_hook(bob, None, bob_data, "acompletion")
            assert await client.get(ns["LOCK_KEY"]) == "bob"

            # release #2: litellm's own except-block calling the failure
            # hook on the SAME (already-released) request_data.
            await lock.async_post_call_failure_hook(data, RuntimeError("upstream error mid-stream"), alice)
            assert await client.get(ns["LOCK_KEY"]) == "bob", "the second release must not touch bob's fresh hold"
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) == "1"
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


def test_contention_redirects_role_calls_and_rejects_direct_calls():
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            alice = _FakeKey("alice")
            bob = _FakeKey("bob")

            holder_data = {"model": "fixture-model-a"}
            await lock.async_pre_call_hook(alice, None, holder_data, "acompletion")

            role_data = {"model": "fixture-role-fast"}
            await lock.async_pre_call_hook(bob, None, role_data, "acompletion")
            assert role_data["model"] == "fixture-primary-model"
            assert await client.get(ns["_ROLE_INFLIGHT_KEY"]) is None, "a redirected call must touch no counter"

            with pytest.raises(ns["HTTPException"]) as exc_info:
                await lock.async_pre_call_hook(bob, None, {"model": "fixture-model-a"}, "acompletion")
            assert exc_info.value.status_code == 429
            assert exc_info.value.detail["held_by"] == "alice"
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


def test_direct_caller_release_is_header_gated():
    async def scenario():
        ns = _load_module()
        client = _new_client(ns)
        await _cleanup(client, ns)
        try:
            lock = ns["FastSubagentLock"](client=client)
            key = _FakeKey("alice")
            data_no_header = {"model": "fixture-model-a"}
            await lock.async_pre_call_hook(key, None, data_no_header, "acompletion")
            await lock.async_post_call_success_hook(data_no_header, key, response=object())
            assert await client.get(ns["LOCK_KEY"]) == "alice", "no header -> must stay held"

            data_with_header = {
                "model": "fixture-model-a",
                "proxy_server_request": {"headers": {"x-subagent-release": "true"}},
            }
            await lock.async_pre_call_hook(key, None, data_with_header, "acompletion")
            await lock.async_post_call_success_hook(data_with_header, key, response=object())
            assert await client.get(ns["LOCK_KEY"]) is None, "explicit header -> must release"
        finally:
            await _cleanup(client, ns)
            await client.aclose()

    _run(scenario())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-rs"]))
