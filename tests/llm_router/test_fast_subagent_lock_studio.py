"""Render fast_subagent_lock.py.j2's STUDIO_* gate and prove the render is
valid, correctly substituted Python — split out of test_fast_subagent_lock.py
(the 4080 gate's own suite) purely to stay under this repo's per-file token
budget (.token-limits.yaml); same render() helper and template, same
constraints on what this CI job can import (no fastapi/litellm/redis — see
that file's module docstring for why).
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
    "llm_router_subagent_lock_model_ids": ["fixture-model-a", "fixture-model-b", "fixture-model-c"],
    "llm_router_redis_port": 6379,
    "llm_router_subagent_lock_role_names": ["fixture-role-fast", "fixture-role-subagent"],
    "llm_router_primary_model": "fixture-primary-model",
    "llm_router_studio_lock_key": "subagent:studio:lock",
    "llm_router_studio_lock_model_ids": ["fixture-studio-a", "fixture-studio-b"],
    "llm_router_studio_lock_role_names": ["fixture-role-fast", "fixture-role-judge"],
    "llm_router_studio_role_overflow_targets": {"fixture-role-fast": "fixture-terminal-rung"},
    "llm_router_studio_direct_overflow_targets": {"fixture-studio-a": "fixture-terminal-rung"},
}


def _comment_filter(text: str) -> str:
    return "\n".join(f"# {line}" if line else "#" for line in str(text).splitlines())


def render(**overrides) -> str:
    env = jinja2.Environment()
    env.filters["to_json"] = lambda v: json.dumps(v)
    env.filters["comment"] = _comment_filter
    context = {**DEFAULT_CONTEXT, **overrides}
    return env.from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(**context)


# --- THE STUDIO GATE (RED before this PR: none of STUDIO_* existed) ----------


def test_only_the_4080_gate_uses_reject_direct():
    # _reject_direct (the shared raise-on-contention helper) stays exclusive
    # to the 4080 gate — a direct studio caller must NEVER reach it, since
    # the studio's direct path redirects instead of raising (see
    # test_studio_direct_caller_never_raises_redirects_instead).
    source = render()
    reject = source.split("async def _reject_direct")[1].split("async def async_pre_call_hook")[0]
    assert "raise HTTPException(" in reject
    assert 'f"{backend_name} is locked by another caller"' in reject
    assert 'await self._reject_direct(client, LOCK_KEY, caller_id, "llm-4080")' in source
    assert 'await self._reject_direct(client, STUDIO_LOCK_KEY' not in source


def test_studio_direct_caller_never_raises_redirects_instead():
    # The core behavioral fork this round adds: unlike the 4080, a direct
    # studio caller that loses the race is redirected (STUDIO_DIRECT_OVERFLOW),
    # never raised — unconditionally, matching the module docstring's
    # rationale (the studio is shared, an exception here bypasses the
    # Router's own fallback engine).
    source = render()
    pre_call = source.split("async def async_pre_call_hook")[1].split("async def _maybe_release")[0]
    stage_two = pre_call.split("# --- Stage 2")[1]
    assert "raise HTTPException" not in stage_two
    assert "overflow_map = STUDIO_ROLE_OVERFLOW if is_studio_role else STUDIO_DIRECT_OVERFLOW" in stage_two
    assert "overflow = overflow_map.get(original_role if is_studio_role else model)" in stage_two
    assert 'data["model"] = overflow' in stage_two


def test_studio_direct_overflow_reflects_its_own_variable():
    source = render(llm_router_studio_direct_overflow_targets={"best": "codex-subscription"})
    match = re.search(r"STUDIO_DIRECT_OVERFLOW = (\{.*?\})", source)
    assert match, source
    assert json.loads(match.group(1)) == {"best": "codex-subscription"}


def test_studio_never_holds_a_session_both_shapes_share_one_counting_path():
    # The defect this round's second fix closes: a direct studio caller must
    # NOT get the 4080's plain SET-NX session lock (held across calls,
    # released only by TTL/header) — every studio caller, role or direct,
    # goes through the SAME counting acquire-or-refresh path, so a hold
    # auto-releases on that call's own completion and "contended" means a
    # genuinely in-flight different caller.
    source = render()
    pre_call = source.split("async def async_pre_call_hook")[1].split("async def _maybe_release")[0]
    stage_two = pre_call.split("# --- Stage 2")[1]
    assert (
        "outcome = await self._acquire_or_refresh_role(client, STUDIO_LOCK_KEY, _STUDIO_ROLE_INFLIGHT_KEY, "
        "caller_id)" in stage_two
    )
    # No separate direct-only acquire path left at all.
    assert "client.set(STUDIO_LOCK_KEY" not in stage_two
    assert "_refresh_if_holder(client, STUDIO_LOCK_KEY" not in stage_two
    # Metadata is stamped only on an actual hold (outcome == 2), never on a
    # redirected (never-held) attempt.
    assert stage_two.index("if outcome == 0:") < stage_two.index("if outcome == 2:")
    assert stage_two.index("if outcome == 2:") < stage_two.rindex("metadata[_STUDIO_LOCK_PARTICIPATES_KEY] = True")


def test_studio_gated_models_and_role_names_reflect_their_own_variables():
    source = render(
        llm_router_studio_lock_model_ids=["studio-a", "studio-b"],
        llm_router_studio_lock_role_names=["fixture-judge"],
    )
    gated = re.search(r"STUDIO_GATED_MODELS = frozenset\((\[.*?\])\)", source)
    roles = re.search(r"STUDIO_ROLE_NAMES = frozenset\((\[.*?\])\)", source)
    assert gated and json.loads(gated.group(1)) == ["studio-a", "studio-b"]
    assert roles and json.loads(roles.group(1)) == ["fixture-judge"]


def test_studio_lock_key_reflects_its_own_variable_and_differs_from_4080():
    source = render(llm_router_studio_lock_key="other:studio:key")
    assert 'STUDIO_LOCK_KEY = "other:studio:key"' in source
    # The two gates must never share a Redis key, or contention on one would
    # block the other — defeats the whole point of a second, independent gate.
    assert "LOCK_KEY = " in source
    assert 'LOCK_KEY = "other:studio:key"' not in source.split("STUDIO_LOCK_KEY")[0]


def test_studio_role_overflow_reflects_its_own_variable():
    source = render(llm_router_studio_role_overflow_targets={"review-oss": "zai-4.5-flash"})
    match = re.search(r"STUDIO_ROLE_OVERFLOW = (\{.*?\})", source)
    assert match, source
    assert json.loads(match.group(1)) == {"review-oss": "zai-4.5-flash"}


def test_renders_to_syntactically_valid_python_with_studio_gate():
    source = render(
        llm_router_studio_lock_model_ids=["studio-a"],
        llm_router_studio_lock_role_names=["fixture-role-judge"],
        llm_router_studio_role_overflow_targets={"fixture-role-judge": "zai-4.5-flash"},
    )
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        path = f.name
    try:
        py_compile.compile(path, doraise=True)
    finally:
        Path(path).unlink(missing_ok=True)


def test_studio_direct_caller_falls_through_from_stage_one():
    # A caller naming a studio id directly is neither in GATED_MODELS nor
    # ROLE_NAMES (4080), so Stage 1 must fall through to Stage 2 rather than
    # returning early.
    source = render()
    pre_call = source.split("async def async_pre_call_hook")[1].split("async def _maybe_release")[0]
    assert "if model in GATED_MODELS or is_role_call:" in pre_call
    assert "if model not in STUDIO_GATED_MODELS and not is_studio_role:" in pre_call


def test_studio_role_redirects_to_its_overflow_target_not_a_constant():
    # Unlike the 4080 gate (one constant, ROLE_REDIRECT_TARGET), the studio
    # gate's redirect target is per-role — looked up by the ORIGINAL role
    # name, which must be captured before Stage 1 can rewrite data["model"].
    source = render()
    pre_call = source.split("async def async_pre_call_hook")[1].split("async def _maybe_release")[0]
    assert "original_role = model" in pre_call
    assert "overflow = overflow_map.get(original_role if is_studio_role else model)" in pre_call
    assert "if overflow is not None:" in pre_call
    assert '                data["model"] = overflow' in pre_call


def test_studio_role_with_no_overflow_target_queues_rather_than_raises():
    # A role gated on the studio with nothing in STUDIO_ROLE_OVERFLOW (no
    # non-local rung declared for it) must return data unchanged on
    # contention — never raise. Anti-vacuity: the branch must be an
    # else-of-None, not a bare pass-through that also fires when overflow IS
    # set.
    source = render()
    pre_call = source.split("async def async_pre_call_hook")[1].split("async def _maybe_release")[0]
    stage_two = pre_call.split("# --- Stage 2")[1]
    assert "raise HTTPException" not in stage_two
    assert "return data" in stage_two


def test_studio_gate_has_no_shape_key_only_a_participates_key():
    # The studio gate has no "shape" distinction at release time (role vs.
    # direct behave identically) — only _STUDIO_LOCK_PARTICIPATES_KEY, unlike
    # the 4080 gate which still needs _LOCK_SHAPE_KEY to pick between its
    # header-gated session release and its role auto-release.
    source = render()
    assert "_STUDIO_LOCK_SHAPE_KEY" not in source
    assert '_STUDIO_LOCK_PARTICIPATES_KEY = "studio_lock_role_participates"' in source
    assert "metadata[_STUDIO_LOCK_PARTICIPATES_KEY] = True" in source


def test_maybe_release_studio_has_no_header_gated_release_path():
    source = render()
    maybe_release = source.split("async def _maybe_release")[1].split("async def async_post_call_success_hook")[0]
    assert "shape = metadata.get(_LOCK_SHAPE_KEY)" in maybe_release
    # The studio's release is unconditional on the participates flag — no
    # header check, no "direct" branch, unlike the 4080's.
    studio_release = maybe_release.split("_STUDIO_LOCK_PARTICIPATES_KEY")[1]
    assert "_release_requested" not in studio_release
    assert "self._release_role_participant(client, STUDIO_LOCK_KEY, _STUDIO_ROLE_INFLIGHT_KEY, caller_id)" in (
        maybe_release
    )
    assert "self._release_if_holder(client, STUDIO_LOCK_KEY" not in maybe_release


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
