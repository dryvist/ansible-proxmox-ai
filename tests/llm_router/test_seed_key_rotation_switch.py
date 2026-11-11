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

import re
from pathlib import Path

import yaml
from jinja2 import Environment

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


def test_delete_task_treats_an_absent_alias_as_done() -> None:
    delete_start = SEED_KEYS_SOURCE.index(
        "- name: Delete the live key for every alias flagged for rotation"
    )
    delete_end = SEED_KEYS_SOURCE.index("\n- name:", delete_start + 1)
    delete_task = SEED_KEYS_SOURCE[delete_start:delete_end]

    assert "status_code: [200, 201, 404]" in delete_task
    status_code_lists = re.findall(r"status_code:\s*\[([^\]]*)\]", delete_task)
    assert status_code_lists == ["200, 201, 404"]
    assert all(
        code == 404 or not 400 <= code <= 599
        for status_code_list in status_code_lists
        for code in (int(value.strip()) for value in status_code_list.split(","))
    )


def test_keys_are_re_read_after_a_rotation_delete() -> None:
    """The re-read drops the rotated alias from the live snapshot, so the create
    (which runs only for aliases absent from that snapshot) re-mints it, and the
    reconcile tasks after it diff against the same live read. See
    test_seed_key_live_aliases.yml for the create's alias-absent condition."""
    delete_idx = SEED_KEYS_SOURCE.index("/key/delete")
    reread_idx = SEED_KEYS_SOURCE.index(
        "Re-read the live keys when a rotation delete ran"
    )
    create_idx = SEED_KEYS_SOURCE.index(
        "Create the caller keys that have a seeded value"
    )
    assert delete_idx < reread_idx < create_idx


def test_rotation_refuses_an_alias_it_cannot_re_mint_before_the_delete() -> None:
    tasks = yaml.safe_load(SEED_KEYS_SOURCE)
    names = [task.get("name") for task in tasks]
    guard_name = "Assert every alias flagged for rotation has a valid replacement value"
    assert names.index(guard_name) < names.index("Delete the live key for every alias flagged for rotation")
    guard = tasks[names.index(guard_name)]
    expression = guard["vars"]["_llm_router_unrecreatable_rotate_aliases"].strip()[2:-2]
    environment = Environment()
    environment.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
    refused = environment.compile_expression(expression)
    keys = [
        {"alias": "good", "value": "sk-" + "a" * 13},
        {"alias": "short", "value": "sk-short"},
        {"alias": "unprefixed", "value": "a" * 20},
        {"alias": "empty", "value": ""},
    ]

    assert refused(llm_router_rotate_key_aliases=["good"], llm_router_virtual_keys=keys) == []
    assert refused(
        llm_router_rotate_key_aliases=["good", "short", "unprefixed", "empty", "undeclared"],
        llm_router_virtual_keys=keys,
    ) == ["short", "unprefixed", "empty", "undeclared"]
    assert guard["when"] == "(llm_router_rotate_key_aliases | default([]) | length) > 0"
