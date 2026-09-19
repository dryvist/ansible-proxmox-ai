"""Render fast_subagent_lock.py.j2 and prove the render is valid, correctly
substituted Python — without importing fastapi/litellm/redis, none of which
this repo's pytest job installs (.github/workflows/_llm-router-contract.yml:
`pip install ansible pytest pyyaml`). `compile()` parses and byte-compiles
source without executing it, so it never touches those imports; this is the
one check that can run in this CI job at all for a file whose real behavior
only exists inside the litellm[proxy] venv on a converged host — see the PR
body for what that leaves unverified.
"""

from __future__ import annotations

import json
import py_compile
import re
import tempfile
from pathlib import Path

import jinja2
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/llm_router/templates/callbacks/fast_subagent_lock.py.j2"

DEFAULT_CONTEXT = {
    "ansible_managed": "Managed by Ansible",
    "llm_router_subagent_lock_key": "subagent:4080:lock",
    "llm_router_subagent_lock_ttl_seconds": 300,
    "llm_router_subagent_lock_release_header": "x-subagent-release",
    # Synthetic, not real registry values — tests/llm_router is a projection
    # zone (test_registry_retype_scan.py): a registered client_model_id must
    # be derived, never re-typed as a literal, even in a test fixture.
    "llm_router_subagent_lock_model_ids": ["fixture-model-a", "fixture-model-b", "fixture-model-c"],
    "llm_router_redis_port": 6379,
    "llm_router_subagent_lock_role_names": ["fixture-role-fast", "fixture-role-subagent"],
    "llm_router_primary_model": "fixture-primary-model",
}


def _comment_filter(text: str) -> str:
    return "\n".join(f"# {line}" if line else "#" for line in str(text).splitlines())


def render(**overrides) -> str:
    env = jinja2.Environment()
    env.filters["to_json"] = lambda v: json.dumps(v)
    env.filters["comment"] = _comment_filter
    context = {**DEFAULT_CONTEXT, **overrides}
    return env.from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(**context)


def test_template_exists():
    assert TEMPLATE_PATH.is_file(), TEMPLATE_PATH


def test_renders_to_syntactically_valid_python():
    source = render()
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        path = f.name
    try:
        py_compile.compile(path, doraise=True)
    finally:
        Path(path).unlink(missing_ok=True)


def test_gated_models_reflect_the_registry_derived_list():
    source = render(llm_router_subagent_lock_model_ids=["a", "b", "c"])
    match = re.search(r"GATED_MODELS = frozenset\((\[.*?\])\)", source)
    assert match, source
    assert json.loads(match.group(1)) == ["a", "b", "c"]


def test_empty_gated_models_still_renders_valid_python():
    source = render(llm_router_subagent_lock_model_ids=[])
    match = re.search(r"GATED_MODELS = frozenset\((\[.*?\])\)", source)
    assert match and json.loads(match.group(1)) == []
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        path = f.name
    try:
        py_compile.compile(path, doraise=True)
    finally:
        Path(path).unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("var", "needle"),
    [
        ("llm_router_subagent_lock_key", 'LOCK_KEY = "subagent:4080:lock"'),
        ("llm_router_subagent_lock_ttl_seconds", "LOCK_TTL_SECONDS = 300"),
        ("llm_router_subagent_lock_release_header", 'RELEASE_HEADER = "x-subagent-release"'),
        ("llm_router_redis_port", "_REDIS_PORT = 6379"),
    ],
)
def test_scalar_constants_render_from_their_own_variable(var, needle):
    assert needle in render()
    # Anti-vacuity: the needle must depend on the variable, not be a fixed
    # string every render happens to contain.
    changed = {"llm_router_subagent_lock_key": "other:key", "llm_router_subagent_lock_ttl_seconds": 60,
               "llm_router_subagent_lock_release_header": "x-other", "llm_router_redis_port": 1234}[var]
    assert needle not in render(**{var: changed})


def test_role_names_reflect_their_own_variable():
    source = render(llm_router_subagent_lock_role_names=["x", "y"])
    match = re.search(r"ROLE_NAMES = frozenset\((\[.*?\])\)", source)
    assert match, source
    assert json.loads(match.group(1)) == ["x", "y"]


def test_role_redirect_target_reflects_its_own_variable():
    source = render(llm_router_primary_model="some-primary")
    assert 'ROLE_REDIRECT_TARGET = "some-primary"' in source


def test_role_call_redirects_instead_of_raising():
    # The one behavioral fork this file adds over the original direct-caller
    # lock: a role-call contention path must rewrite data["model"] and
    # return, never reach the `raise HTTPException` branch.
    source = render()
    pre_call = source.split("async def async_pre_call_hook")[1].split("async def async_post_call_success_hook")[0]
    assert 'data["model"] = ROLE_REDIRECT_TARGET' in pre_call
    # The redirect branch must be reached BEFORE the raise, not after it
    # (dead code that never runs on the intended path).
    assert pre_call.index('data["model"] = ROLE_REDIRECT_TARGET') < pre_call.index("raise HTTPException")


def test_refresh_and_release_use_atomic_compare_scripts():
    # TOCTOU fix: no bare GET-then-EXPIRE or GET-then-DELETE pair anywhere —
    # both must go through the Lua compare-and-act scripts.
    source = render()
    assert "_REFRESH_IF_HOLDER_SCRIPT" in source
    assert "_RELEASE_IF_HOLDER_SCRIPT" in source
    assert 'redis.call("GET", KEYS[1]) == ARGV[1]' in source
    assert "await client.eval(_REFRESH_IF_HOLDER_SCRIPT" in source
    assert "await client.eval(_RELEASE_IF_HOLDER_SCRIPT" in source
    # The old two-call pattern must be gone from the hook bodies (a plain
    # substring ban is enough here: nothing else in this file has reason to
    # call .expire() or a bare .get()-then-.delete() pair).
    assert "await client.expire(" not in source
    assert "await client.delete(" not in source


def test_role_calls_gate_on_participates_direct_calls_need_the_header():
    # The core semantics this PR's fix depends on: _maybe_release must gate
    # role-call release on _LOCK_PARTICIPATES_KEY (never unconditionally on
    # caller shape alone — see test_role_call_release_requires_participation
    # for why), and direct-call release on the explicit header — mixing
    # these up either breaks multi-call cache-affinity for direct callers,
    # tears down a direct caller's hold out from under it, or leaves
    # fast/subagent's lock held for the full TTL after a fast, successful
    # call.
    source = render()
    maybe_release = source.split("async def _maybe_release")[1].split("async def async_post_call_success_hook")[0]
    assert 'shape = metadata.get(_LOCK_SHAPE_KEY)' in maybe_release
    assert 'if shape == "role":' in maybe_release
    assert "if metadata.pop(_LOCK_PARTICIPATES_KEY, None):" in maybe_release
    assert "if not _release_requested(data):" in maybe_release


def test_role_call_release_requires_participation():
    # Regression guard for the should-fix half of this round: a role call
    # that merely refreshed someone else's hold (outcome 1 — a direct
    # caller's fast-gpu hold) must not set _LOCK_PARTICIPATES_KEY, or its
    # own completion would tear that hold down. Only outcome 2 (fresh
    # acquire, or a refresh of an already role-owned hold) participates.
    source = render()
    pre_call = source.split("async def async_pre_call_hook")[1].split("async def _maybe_release")[0]
    assert "outcome = await self._acquire_or_refresh_role(client, caller_id)" in pre_call
    assert "if outcome == 0:" in pre_call
    assert "if outcome == 2:" in pre_call
    assert "metadata[_LOCK_PARTICIPATES_KEY] = True" in pre_call
    # Anti-vacuity: outcome == 1 must fall through WITHOUT setting the
    # participates flag — assert the flag-setting line is nested only under
    # the outcome == 2 branch, not a bare unconditional assignment.
    assert pre_call.count("metadata[_LOCK_PARTICIPATES_KEY] = True") == 1


def test_role_inflight_counter_gates_release_not_just_holder_match():
    # Case (b) from the module docstring: two role calls from the same
    # caller sharing a hold must not have the first one to finish delete it
    # out from under the second, still in-flight, one. The release script
    # must DECR a counter and only DEL once it reaches zero, not delete on
    # a bare holder match the way the direct-caller scripts do.
    source = render()
    assert "_ACQUIRE_OR_REFRESH_ROLE_SCRIPT" in source
    assert "_RELEASE_ROLE_PARTICIPANT_SCRIPT" in source
    assert 'local n = redis.call("DECR", KEYS[2])' in source
    assert "if n <= 0 then" in source
    assert "_ACQUIRE_OR_REFRESH_ROLE_SCRIPT, 2, LOCK_KEY, _ROLE_INFLIGHT_KEY, caller_id, LOCK_TTL_SECONDS" in source
    assert "_RELEASE_ROLE_PARTICIPANT_SCRIPT, 2, LOCK_KEY, _ROLE_INFLIGHT_KEY, caller_id" in source


def test_release_is_idempotent_against_a_double_call():
    # Regression guard: an upstream exception mid-stream reaches BOTH the
    # streaming iterator hook's `finally` and, afterwards,
    # async_post_call_failure_hook on the SAME request_data dict. Without
    # consuming the participates flag, the second call would decrement the
    # role-inflight counter a second time for one call, stealing a share
    # that belongs to a genuinely separate, still in-flight sibling. The fix
    # is to pop (not get) the flag, so the second call is a no-op — and the
    # release script itself must also refuse to DECR a counter that has
    # already expired/vanished (a long role stream plus a direct caller's
    # own EXPIRE-only refreshes can let the counter disappear first).
    source = render()
    maybe_release = source.split("async def _maybe_release")[1].split("async def async_post_call_success_hook")[0]
    assert "if metadata.pop(_LOCK_PARTICIPATES_KEY, None):" in maybe_release
    assert 'if metadata.get(_LOCK_PARTICIPATES_KEY):' not in maybe_release
    assert 'if redis.call("EXISTS", KEYS[2]) == 0 then\n  return 0\nend' in source


def test_streaming_iterator_hook_is_a_real_override_that_releases():
    # BLOCKER: on litellm[proxy]==1.98.0, async_post_call_success_hook is
    # never reached for a streaming request — see the method's own
    # docstring for the source citations. Every real client in this estate
    # streams, so this override is required, not optional, and it must be
    # a real async-generator method (ProxyLogging only wraps the stream
    # through it when a callback's own class attrs define it) that still
    # yields every item and releases even if the caller aborts mid-stream.
    source = render()
    assert "async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data" in source
    streaming_hook = source.split("async def async_post_call_streaming_iterator_hook")[1].split(
        "async def async_post_call_failure_hook"
    )[0]
    assert "async for item in response:" in streaming_hook
    assert "yield item" in streaming_hook
    assert "finally:" in streaming_hook
    assert "await self._maybe_release(request_data, user_api_key_dict)" in streaming_hook
    # The release must be reached via `finally`, not only on the happy path
    # — anti-vacuity: the finally block must come after the try, and the
    # release call must be inside the finally, not merely present anywhere
    # in the method.
    assert streaming_hook.index("finally:") < streaming_hook.rindex(
        "await self._maybe_release(request_data, user_api_key_dict)"
    )


def test_release_eligibility_reads_metadata_not_model():
    # Regression guard for the coupled blockers-2-and-3 fix: release
    # eligibility must NOT be re-derived from data["model"] at post-call
    # time, because the Router's own ordinary fallback chain can rewrite
    # that field before async_post_call_success_hook ever sees it — which
    # would silently defeat release for a fast/subagent call that fell
    # through past hermes-local-4080 to a later rung. The pre-call hook must
    # stamp the shape into metadata before anything else can touch model.
    source = render()
    pre_call = source.split("async def async_pre_call_hook")[1].split("async def _maybe_release")[0]
    assert 'metadata = data.setdefault("metadata", {})' in pre_call
    assert 'metadata[_LOCK_SHAPE_KEY] = "role" if is_role_call else "direct"' in pre_call
    maybe_release = source.split("async def _maybe_release")[1].split("async def async_post_call_success_hook")[0]
    assert 'data.get("model")' not in maybe_release


def test_success_and_failure_hooks_both_call_maybe_release():
    source = render()
    assert "await self._maybe_release(data, user_api_key_dict)" in source.split(
        "async def async_post_call_success_hook"
    )[1].split("async def async_post_call_streaming_iterator_hook")[0]
    failure_hook = source.split("async def async_post_call_failure_hook")[1]
    # request_data, not data — matches litellm's documented
    # async_post_call_failure_hook signature (docs.litellm.ai/docs/proxy/call_hooks).
    assert "await self._maybe_release(request_data, user_api_key_dict)" in failure_hook
    # Defensive even with a docs-confirmed signature (not verified against a
    # live process on the pinned release): the failure hook must never let
    # an exception escape, which would interfere with the actual failure
    # response reaching the caller — more important than releasing early.
    assert "except Exception" in failure_hook


def test_redis_import_is_guarded_not_unconditional():
    # The whole point of the try/except is that this file must stay
    # importable in an environment without the redis package (this pytest
    # job, notably) — a regression here would break every test above too,
    # but assert the guard shape directly so a future edit that drops the
    # try/except fails HERE with a clear message instead of a confusing
    # collection error somewhere else.
    source = render()
    assert "try:\n    import redis.asyncio as redis_asyncio" in source
    assert "except ImportError" in source


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
