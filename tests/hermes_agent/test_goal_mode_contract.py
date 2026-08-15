from __future__ import annotations

import yaml

from conftest import REPO_ROOT, ROLE_ROOT, _task, role_defaults


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
    router_defaults = role_defaults(REPO_ROOT / "roles" / "llm_router")
    registry = yaml.safe_load((REPO_ROOT / "llm-models.yml").read_text())[
        "llm_router_model_registry"
    ]
    router_config = (REPO_ROOT / "roles/llm_router/templates/config.yaml.j2").read_text()
    config = (ROLE_ROOT / "templates" / "config.yaml.j2").read_text()

    hermes_alias = "hermes-default"
    # Physical ids live in ONE file — the repo-root llm-models.yml registry —
    # and the router's selector vars are projections of it. Pinning literals
    # here is what let all four aliases drift to unroutable models at once
    # (2026-07-28, every one a live 404), so follow the indirection to its
    # source instead of re-pinning the ids under a new name.
    by_role = {
        entry["serving_role"]: entry
        for entry in registry
        if entry.get("enabled") and "serving_role" in entry
    }
    hermes_backend = by_role["primary"]["client_model_id"]
    # The judge follows its ALIAS, not a serving_role. `small` names the
    # trivial-task tier and held the judge until 2026-08-15; the two came apart
    # when the judge moved to a resident backend to escape the small tier's
    # cold load, and deriving from serving_role here would have silently kept
    # asserting the old wiring.
    judge_backend = next(
        entry["client_model_id"]
        for entry in registry
        if entry.get("enabled") and "goal-judge" in entry.get("stable_aliases", [])
    )
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
    assert defaults["hermes_agent_brain_sync_enabled"] is False
    # An alias belongs to the entry it points at, so the whole consumer-facing
    # name set is readable off the registry — and cannot name a model that is
    # not there. A model_list deployment entry named after an alias is banned
    # (AGENTS.md): the duplicate config drifts from the real backend every time
    # the model changes.
    aliases = {
        alias: entry["client_model_id"]
        for entry in registry
        if entry.get("enabled")
        for alias in entry.get("stable_aliases", [])
    }
    assert aliases == {
        hermes_alias: hermes_backend,
        "tool-calling": hermes_backend,
        "goal-judge": judge_backend,
        "interim-brain": hermes_backend,
    }
    # Both selectors must be declared servable, or the alias indirection just
    # moves the 404 one level down.
    #
    # The cluster model is servable ONLY while the cluster leg is actually
    # available. It is hermes-default's router_settings.fallbacks target while
    # a cluster window is up, and an unroutable fallback target 502s instead of
    # failing over — which is exactly what it did, unnoticed, from 2026-08-05
    # (both hosts' clusterMode disabled, TB cable out) until #365.
    #
    # Derive the expectation from llm_router_cluster_leg_available rather than
    # re-pinning a literal: that var is the single switch #365 introduced, and
    # roles/llm_router/tasks/assert-cluster-leg.yml already fails the converge
    # if it and the registry's `servable` disagree. Following it here means
    # this test tracks the leg coming back instead of going red the moment it
    # does — re-pinning a literal is the drift this whole indirection exists
    # to prevent.
    #
    # `servable` is deliberately NOT `enabled`: every large-tier entry is
    # enabled (the router offers it), only these are servable (the backend
    # answers for it). Conflating them yields a 404, not an answer.
    #
    # The contract is a BICONDITIONAL — servable if and only if the entry names
    # a serving_role — and BOTH sides derive from the registry. This used to
    # name the expected ids through by_role["primary"]/["small"], which held
    # only while the serving host ran exactly one warm model: since 2026-08-14
    # it holds two, and a second servable model with no role to name it would
    # have failed a true statement. Deriving keeps the check real rather than
    # loosening it — flipping `servable` on a dead entry, or dropping it from a
    # live one, still fails here.
    expected_servable = [
        entry["client_model_id"]
        for entry in registry
        if entry.get("enabled")
        and "serving_role" in entry
        and (
            entry["serving_role"] != "cluster"
            or router_defaults["llm_router_cluster_leg_available"]
        )
    ]
    assert [
        entry["client_model_id"] for entry in registry if entry.get("servable")
    ] == expected_servable
    # Both selectors the fabric actually points at must be in that set, named
    # through by_role rather than by literal.
    assert hermes_backend in expected_servable
    assert judge_backend in expected_servable
    hermes_entries = [
        entry for entry in registry if entry["client_model_id"] == hermes_backend
    ]
    assert len(hermes_entries) == 1
    assert hermes_entries[0]["context_window"] == 65536
    # The registry is the SOLE spelling of a model name or key field: the role's
    # defaults project it and must never re-type one. A literal here is exactly
    # the drift this indirection exists to prevent, so it fails the build rather
    # than waiting for a live 404. Values only — the defaults' prose may of
    # course still discuss the tiers.
    router_defaults_values = yaml.dump(router_defaults, allow_unicode=True)
    for entry in registry:
        for field in ("client_model_id", "upstream_model_id", "key_field"):
            if field in entry:
                assert entry[field] not in router_defaults_values, (
                    f"{entry[field]} is re-typed in roles/llm_router/defaults/main.yml; "
                    "derive it from llm-models.yml instead"
                )
    assert router_defaults["llm_router_num_retries"] == 0
    # 429 = "the slot is busy", never "the work is impossible", so the router
    # absorbs it rather than failing the caller (#175). Not 0 — that setting
    # killed a cron mid-generation on 2026-07-24.
    assert router_defaults["llm_router_rate_limit_retries"] == 8
    assert "model_group_alias:" in router_config
    assert "llm_router_model_group_aliases.items()" in router_config
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
