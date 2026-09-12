from __future__ import annotations

import yaml

from conftest import REPO_ROOT, ROLE_ROOT, _task, role_defaults
from _registry import backend_for_alias, backend_for_role, load_registry
from _role_files import template_text
from _cron_pool_ceiling_shared import (
    router_request_timeout_seconds,
    wall_timeout_seconds,
)


# test_enqueuer_goal_flags_follow_the_role_toggle DELETED (native-cron
# reframe, 18/18): kanban-enqueue-recurring.sh.j2 and every Kanban card
# (including docs-sync) are gone — there is no enqueuer template and no
# per-card `channel:` override left to test at all. hermes_agent_kanban_cards
# no longer exists; hermes_agent_kanban_goal_mode still governs ad-hoc/
# follow-up kanban work (the reviewer job filing a gap card, etc.), asserted
# elsewhere in this file against the Python patches, not against a template.
#
# test_the_kanban_card_body_still_carries_the_evidence_and_block_contract was
# removed: kanban-card-body.md.j2 no longer exists (18/18 to cron). Its
# kanban_block(kind=needs_input) escalation and self-directed `hermes send`
# instruction were Kanban-task machinery — a cron job has no task to block and
# already delivers natively via `--deliver`, so neither applies. The one piece
# of real value in that wrapper, the evidence-contract anti-fabrication
# instruction (cite the query, never invent a number), is NOT reproduced
# anywhere for the 18 converted jobs — flagged in the PR, not silently lost.


def test_reviewer_prompt_carries_no_leftover_self_perpetuation() -> None:
    """The native-cron redesign made the reviewer's own next-occurrence
    pre-create (create the next slot blocked, have the enqueuer unblock it)
    unnecessary: the crontab/cron entry is now the time gate for every job,
    reviewer included — it is a plain hermes_agent_direct_cron_jobs entry, not
    a kanban card. Pins that the chain-continuation step actually left the
    prompt, rather than merely stopped being tested: no goal_mode Jinja
    conditional exists in it at all any more, so it renders identically
    regardless of hermes_agent_kanban_goal_mode.
    """
    defaults = role_defaults(ROLE_ROOT)
    prompt = str(defaults["hermes_agent_reviewer_card_prompt"])
    assert "{%" not in prompt, "no Jinja conditionals should remain in the reviewer prompt"
    assert "initial_status=blocked" not in prompt
    assert "goal_mode" not in prompt
    assert "bounded goal loop" not in prompt
    assert prompt.strip().endswith(
        'Save the updated gap fingerprint back to "review-last".'
    )


def test_hermes_inference_paths_use_the_declared_alias() -> None:
    defaults = role_defaults(ROLE_ROOT)
    group_vars = yaml.safe_load((REPO_ROOT / "inventory/group_vars/all.yml").read_text())
    hindsight_group_vars = yaml.safe_load(
        (REPO_ROOT / "inventory/group_vars/hindsight_group.yml").read_text()
    )
    hindsight_compose = (
        REPO_ROOT / "roles/hindsight_docker/templates/docker-compose.yml.j2"
    ).read_text()
    registry = load_registry()
    config = (ROLE_ROOT / "templates" / "config.yaml.j2").read_text()
    environment = template_text(ROLE_ROOT, "hermes-env.j2")

    hermes_alias = "hermes-default"
    # Physical ids live in ONE place — the repo-root llm-models.d/ registry —
    # and the router's selector vars are projections of it. Pinning literals
    # here is what let all four aliases drift to unroutable models at once
    # (2026-07-28, every one a live 404), so follow the indirection to its
    # source instead of re-pinning the ids under a new name.
    hermes_backend = backend_for_role(registry, "primary")
    # The judge follows its ALIAS, not a serving_role. `small` names the
    # trivial-task tier and held the judge until 2026-08-15; the two came apart
    # when the judge moved to a resident backend to escape the small tier's
    # cold load, and deriving from serving_role here would have silently kept
    # asserting the old wiring.
    judge_backend = backend_for_alias(registry, group_vars["hermes_goal_judge_model"])
    assert group_vars["hermes_brain_model"] == hermes_alias
    # The judge rides its own alias now — a judge on the worker's model is
    # self-preference bias, and the two serialize against one serving slot.
    assert group_vars["hermes_goal_judge_model"] == "goal-judge"
    assert judge_backend != hermes_backend
    assert defaults["hermes_agent_model"] == "{{ hermes_brain_model }}"
    assert defaults["hermes_agent_compression_model"] == "{{ hermes_brain_model }}"
    assert defaults["hermes_agent_memory_llm_model"] == "{{ hermes_brain_model }}"
    assert hindsight_group_vars["hindsight_docker_llm_model"] == "{{ hermes_brain_model }}"
    assert 'HINDSIGHT_API_LLM_MODEL: "{{ hindsight_docker_llm_model }}"' in hindsight_compose
    assert defaults["hermes_agent_model_max_tokens"] == 8192
    assert defaults["hermes_agent_context_compression_threshold"] == 0.75
    assert defaults["hermes_agent_stream_stale_timeout"] == 900
    # The non-stream stale bound tracks the streaming one rather than carrying
    # its own literal: both guard the same fabric against the same 90s remote
    # default the FQDN router misclassification leaves in place.
    assert (
        defaults["hermes_agent_api_call_stale_timeout"]
        == "{{ hermes_agent_stream_stale_timeout }}"
    )
    assert (
        "HERMES_API_CALL_STALE_TIMEOUT={{ hermes_agent_api_call_stale_timeout }}"
        in environment
    )
    assert defaults["hermes_agent_cron_inactivity_timeout_seconds"] == 1800
    # hermes_agent_cron_wall_timeout_seconds is now derived from the store's
    # own schedules (defaults/main/20-brain-and-slack.yml) rather than a
    # literal — see test_cron_pool_ceiling.py for the formula's own coverage.
    assert wall_timeout_seconds() < router_request_timeout_seconds()
    assert (
        "HERMES_CRON_TIMEOUT={{ hermes_agent_cron_inactivity_timeout_seconds }}"
        in environment
    )
    assert (
        "HERMES_CRON_WALL_TIMEOUT={{ hermes_agent_cron_wall_timeout_seconds }}"
        in environment
    )
    assert defaults["hermes_agent_brain_sync_enabled"] is False
    # Reads the alias, not the worker model. This was pinned to
    # hermes_agent_model until 2026-08-15 for a measured reason — `goal-judge`
    # resolved to a swap-class backend whose ~79s cold load exceeded the judge
    # timeout — and the stated precondition for flipping it was a residency fix
    # in the serving host, which landed with maxResidentWorkers = 2. The judge
    # backend is now pinned resident, so the cold-load case cannot occur.
    #
    # Pinning it back to the worker model reintroduces self-preference bias
    # AND makes judge and worker share one model; do not do it without
    # re-measuring what changed.
    assert defaults["hermes_agent_kanban_goal_judge_model"] == "{{ hermes_goal_judge_model }}"
    assert defaults["hermes_agent_kanban_goal_judge_timeout_seconds"] == 150
    assert "goal_judge:" in config
    assert "model: {{ hermes_agent_kanban_goal_judge_model | to_json }}" in config
    assert "base_url: '{{ hermes_agent_model_base_url }}'" in config


def test_group_vars_reads_canonical_zammad_mcp_pair() -> None:
    group_vars = (REPO_ROOT / "inventory/group_vars/hermes_agent_group.yml").read_text()
    assert "bao_local_llm_secrets.ZAMMAD_MCP_URL" in group_vars
    assert "bao_local_llm_secrets.ZAMMAD_MCP_TOKEN" in group_vars
    assert "bao_local_llm_secrets.ZAMMAD_API_TOKEN" not in group_vars
    assert "ZAMMAD_MCP_URL | regex_replace('/api/v1/?$', '')" in group_vars
    assert "else lookup('env', 'ZAMMAD_URL')" in group_vars


def test_prompt_catalog_build_keeps_a_gc_root() -> None:
    build_task = _task("Build the pinned prompt catalog on the controller")
    command = build_task["ansible.builtin.command"]["cmd"]
    assert "--out-link /tmp/hermes-agent-prompts" in command
    assert "--no-link" not in command
