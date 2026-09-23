"""LLM_ROUTER_MASTER_KEY is the router's own admin credential, never a
consumer's. Every consumer gets its own per-caller virtual key
(roles/llm_router/defaults/main/56-virtual-keys.yml) instead. This pins that
contract: the literal env-var name must appear nowhere in the tree except
inside roles/llm_router itself.

This is the regression guard for a real incident: agentgateway_docker,
dify_docker, langfuse_docker, langgraph_docker, llamaindex and herdr_server
all read this value directly or through inventory/group_vars/all.yml's
now-deleted ai_orchestration_model_api_key, so every consumer's spend was
unattributable and revoking one tool meant rotating everyone.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NEEDLE = "LLM_ROUTER_MASTER_KEY"
ALLOWED_ROLE = "llm_router"
SUFFIXES = {".yml", ".yaml", ".j2"}
# Scanned as plain text, not just roles/: a consumer could just as easily
# reintroduce this at the inventory layer, which is exactly how it leaked
# before (inventory/group_vars/all.yml's ai_orchestration_model_api_key).
SCAN_DIRS = ("roles", "inventory", "playbooks")


def _offenders():
    for scan_dir in SCAN_DIRS:
        base = REPO_ROOT / scan_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT)
            if rel.parts[0] == "roles" and rel.parts[1] == ALLOWED_ROLE:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if NEEDLE in text:
                line = text[: text.index(NEEDLE)].count("\n") + 1
                yield f"{rel}:{line}"


def test_no_consumer_references_the_master_key():
    offenders = sorted(_offenders())
    assert not offenders, (
        f"{NEEDLE} must appear only inside roles/{ALLOWED_ROLE}/ — every other "
        "consumer reads its own scoped virtual key "
        "(roles/llm_router/defaults/main/56-virtual-keys.yml). Found it in:\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_can_actually_see_a_known_violation():
    """Anti-vacuity: prove the scan finds the needle where it IS allowed,
    so a clean result elsewhere means 'not present', not 'nothing scanned'."""
    allowed_hits = [
        p
        for p in (REPO_ROOT / "roles" / ALLOWED_ROLE).rglob("*")
        if p.suffix in SUFFIXES
        and p.is_file()
        and NEEDLE in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert allowed_hits, (
        f"expected at least one {NEEDLE} reference inside roles/{ALLOWED_ROLE}/ "
        "(its own admin/config rendering) — the scan or the fixture drifted"
    )
