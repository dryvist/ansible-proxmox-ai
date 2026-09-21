"""seed-keys.yml's on-demand rotation switch (llm_router_rotate_key_aliases).

A minted key's `key` value is a LiteLLM primary identifier with no
"update the value" endpoint, so rotation is delete-then-mint. This is off
(empty list) on every ordinary converge; an operator names the alias(es) to
rotate right after the matching OpenBao value has actually changed
(ansible-proxmox-apps' openbao_seed_rotate_fields switch on the same field).

Pins the shape rather than mocking a live proxy, matching
test_seed_key_live_aliases.yml's source-assertion style for this file.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "llm_router"

SEED_KEYS_SOURCE = (ROLE_ROOT / "tasks" / "seed-keys.yml").read_text()
VIRTUAL_KEYS_DEFAULTS = (ROLE_ROOT / "defaults" / "main" / "56-virtual-keys.yml").read_text()


def test_rotate_switch_defaults_to_an_empty_list() -> None:
    """No aliases named -> the delete task's `when` never fires -> no-op."""
    assert "llm_router_rotate_key_aliases: []" in VIRTUAL_KEYS_DEFAULTS


def test_delete_task_deletes_by_alias_not_by_token() -> None:
    """key_aliases (not keys/token) — this role never reads a live key's
    token back, only its alias, so deletion must not require one."""
    assert "/key/delete" in SEED_KEYS_SOURCE
    assert "key_aliases: \"{{ llm_router_rotate_key_aliases | default([]) }}\"" in SEED_KEYS_SOURCE


def test_delete_task_is_gated_on_a_non_empty_rotate_list() -> None:
    assert "when: (llm_router_rotate_key_aliases | default([]) | length) > 0" in SEED_KEYS_SOURCE


def test_keys_are_re_read_after_a_rotation_delete() -> None:
    """Without a re-read, the deleted alias is still in
    _llm_router_live_key_aliases from the FIRST /key/list call, so the
    'unseeded keys' computation right after would skip re-minting it."""
    delete_idx = SEED_KEYS_SOURCE.index("/key/delete")
    reread_idx = SEED_KEYS_SOURCE.index(
        "Re-read the live keys when a rotation delete ran"
    )
    unseeded_idx = SEED_KEYS_SOURCE.index("Compute the virtual keys still needing to be minted")
    assert delete_idx < reread_idx < unseeded_idx
