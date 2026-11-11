"""The key seed selector scopes every key-level write without a live router."""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "llm_router"
TASK_ROOT = ROLE_ROOT / "tasks"
DEFAULTS = yaml.safe_load(
    (ROLE_ROOT / "defaults/main/56-virtual-keys.yml").read_text(encoding="utf-8")
)
VIRTUAL_KEY_DEFAULTS = yaml.safe_load(
    (ROLE_ROOT / "defaults/main/60-virtual-keys.yml").read_text(encoding="utf-8")
)
SEED_KEYS = yaml.safe_load((TASK_ROOT / "seed-keys.yml").read_text(encoding="utf-8"))


def _named_task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _assertion(tasks: list[dict], name: str) -> dict:
    return _named_task(tasks, name)["ansible.builtin.assert"]


def _evaluate(expression: str, **context: object) -> object:
    return Environment().compile_expression(expression)(**context)


def _uri_tasks(value: object) -> list[dict]:
    if isinstance(value, list):
        return [task for item in value for task in _uri_tasks(item)]
    if isinstance(value, dict):
        own = [value] if "ansible.builtin.uri" in value else []
        return own + [task for item in value.values() for task in _uri_tasks(item)]
    return []


def test_default_selection_is_empty_and_keeps_every_declared_key() -> None:
    assert DEFAULTS["llm_router_seed_key_aliases"] == []
    expression = VIRTUAL_KEY_DEFAULTS["_llm_router_seed_virtual_keys"]
    keys = [{"alias": "first", "value": "sk-test-first"}, {"alias": "second", "value": "sk-test-second"}]

    selected = _evaluate(expression[2:-2].strip(), llm_router_virtual_keys=keys, llm_router_seed_key_aliases=[])

    assert selected == keys


def test_named_selection_keeps_only_the_requested_declared_key() -> None:
    expression = VIRTUAL_KEY_DEFAULTS["_llm_router_seed_virtual_keys"]
    keys = [{"alias": "first", "value": "sk-test-first"}, {"alias": "second", "value": "sk-test-second"}]

    selected = _evaluate(expression[2:-2].strip(), llm_router_virtual_keys=keys, llm_router_seed_key_aliases=["second"])

    assert selected == [keys[1]]


def test_undeclared_alias_assertion_precedes_the_first_import_with_a_write() -> None:
    task = _assertion(SEED_KEYS, "Validate selected key aliases are declared")
    condition = task["that"][0]
    declared = ["first", "second"]

    projection = [{"alias": alias} for alias in declared]
    assert _evaluate(
        condition, llm_router_seed_key_aliases=["first"], _llm_router_virtual_keys_without_values=projection
    )
    assert _evaluate(condition, llm_router_seed_key_aliases=[], _llm_router_virtual_keys_without_values=projection)
    assert not _evaluate(
        condition, llm_router_seed_key_aliases=["missing"], _llm_router_virtual_keys_without_values=projection
    )
    assert "undeclared alias(es)" in task["fail_msg"]

    assertion_index = next(
        index for index, item in enumerate(SEED_KEYS)
        if item.get("name") == "Validate selected key aliases are declared"
    )
    first_team_write_index = next(
        index for index, item in enumerate(SEED_KEYS)
        if item.get("ansible.builtin.import_tasks") == "seed-priority-teams.yml"
    )
    priority_tasks = yaml.safe_load((TASK_ROOT / "seed-priority-teams.yml").read_text(encoding="utf-8"))
    assert any("ansible.builtin.uri" in item and item["ansible.builtin.uri"].get("method") == "POST" for item in priority_tasks)
    assert not any(
        "ansible.builtin.uri" in item
        and item["ansible.builtin.uri"].get("method", "GET") == "POST"
        for item in SEED_KEYS[:first_team_write_index]
    )
    assert assertion_index < first_team_write_index


def test_rotation_alias_must_be_in_a_non_empty_selection() -> None:
    rotation_task = _named_task(SEED_KEYS, "Validate rotated key aliases are selected")
    task = rotation_task["ansible.builtin.assert"]
    condition = task["that"][0]

    assert _evaluate(
        condition,
        llm_router_rotate_key_aliases=["first"],
        llm_router_seed_key_aliases=["first"],
    )
    assert not _evaluate(
        condition,
        llm_router_rotate_key_aliases=["second"],
        llm_router_seed_key_aliases=["first"],
    )
    assert "llm_router_seed_key_aliases" in task["fail_msg"]
    assert rotation_task["when"] == "_llm_router_seed_selected | bool"
    rotation_assert_index = SEED_KEYS.index(rotation_task)
    first_write_index = next(
        index for index, item in enumerate(SEED_KEYS)
        if item.get("ansible.builtin.import_tasks") == "seed-priority-teams.yml"
    )
    assert rotation_assert_index < first_write_index


def test_key_write_paths_are_scoped_by_the_filtered_selection() -> None:
    create = _named_task(SEED_KEYS, "Create the caller keys that have a seeded value (an existing alias is not a failure)")
    assert "_llm_router_seed_virtual_keys" in create["loop"]

    teamless = yaml.safe_load((TASK_ROOT / "reconcile-teamless-keys.yml").read_text(encoding="utf-8"))
    teamless_loop = _named_task(teamless, "Reconcile ownership for every key changing teams independently")
    # An empty selection keeps every live teamless key in scope (unregistered keys included).
    assert "_llm_router_seed_selected" in teamless_loop["when"]
    teamless_child = yaml.safe_load((TASK_ROOT / "reconcile-teamless-key.yml").read_text(encoding="utf-8"))
    assert any(
        task["ansible.builtin.uri"].get("method") == "POST"
        and "/key/update" in task["ansible.builtin.uri"]["url"]
        for task in _uri_tasks(teamless_child)
    )

    policy = yaml.safe_load((TASK_ROOT / "reconcile-seeded-key-policy.yml").read_text(encoding="utf-8"))
    compute = _named_task(policy, "Compute the key policy update each live key still needs, if any")
    assert "_llm_router_seed_virtual_keys" in compute["ansible.builtin.set_fact"]["_llm_router_key_updates"]
    update = _named_task(policy, "Reconcile caller policy for live keys that changed")
    assert update["loop"] == "{{ _llm_router_key_updates }}"

    # Completing the live-key snapshot is intentionally read-only and still sees every page.
    pages = yaml.safe_load((TASK_ROOT / "seed-key-pages.yml").read_text(encoding="utf-8"))
    assert all(task.get("ansible.builtin.uri", {}).get("method", "GET") == "GET" for task in pages if "ansible.builtin.uri" in task)


def test_partial_selection_only_creates_missing_teams_used_by_selected_keys() -> None:
    priority = yaml.safe_load((TASK_ROOT / "seed-priority-teams.yml").read_text(encoding="utf-8"))
    create_priority = _named_task(priority, "Create absent priority teams")
    assert "item.status == 404" in str(create_priority["when"])
    assert "item.item.team_id in" in str(create_priority["when"])
    assert "_llm_router_seed_team_ids" in str(create_priority["when"])
    update_priority = _named_task(priority, "Reconcile existing priority team tags")
    assert "_llm_router_seed_selected" in str(update_priority["when"])
    assert "not (_llm_router_seed_selected | bool)" in update_priority["when"]

    default_teams = yaml.safe_load((TASK_ROOT / "seed-teams.yml").read_text(encoding="utf-8"))
    update_default = _named_task(default_teams, "Seed managed team model grants through the native team API")
    assert "item.status == 404" in update_default["when"]
    assert "item.item.team_id in" in update_default["when"]
    assert "_llm_router_seed_team_ids" in update_default["when"]
    assert "_llm_router_seed_selected" in update_default["when"]


def test_rebuild_budget_and_rpm_resync_use_the_seed_projection() -> None:
    budgets = yaml.safe_load((TASK_ROOT / "reconcile-key-budgets.yml").read_text(encoding="utf-8"))
    budgeted = _named_task(budgets, "Compute the budgeted keys to reconcile in rebuild mode")
    assert "_llm_router_seed_virtual_keys" in budgeted["ansible.builtin.set_fact"]["_llm_router_budgeted_keys"]
    rate_limited = next(
        task for task in budgets if "_llm_router_rate_limited_keys" in task.get("ansible.builtin.set_fact", {})
    )
    assert "_llm_router_seed_virtual_keys" in rate_limited["ansible.builtin.set_fact"]["_llm_router_rate_limited_keys"]


def test_selection_state_is_derived_once() -> None:
    # Task files read _llm_router_seed_selected; none re-derives it from the alias list length.
    for path in TASK_ROOT.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "llm_router_seed_key_aliases | default([]) | length) == 0" not in text, path.name
        assert "llm_router_seed_key_aliases | default([]) | length) > 0" not in text, path.name


def test_selected_seed_run_skips_the_app_agent_probe() -> None:
    consumers = yaml.safe_load((TASK_ROOT / "seed-consumers.yml").read_text(encoding="utf-8"))
    probe = _named_task(consumers, "Probe app-agent tool and server boundaries after converge")
    assert probe["when"] == "not (_llm_router_seed_selected | bool)"
