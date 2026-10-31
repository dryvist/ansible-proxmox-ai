"""Evaluation tags land only in evaluation groups, and only specialist-eval.yml targets those groups.

The tag-to-group expression is rendered from add_lxc_host_inventory.yml by Ansible's Templar, for
offline host fixtures. The prefetch and production groups come from the playbooks, not from this
file, so a change to either shows up here.
"""

from __future__ import annotations

import re

import pytest

from _eval_support import GROUP_VARS, ROOT, load_yaml, render

INVENTORY = ROOT / "inventory/load_tofu/add_lxc_host_inventory.yml"
PLAYBOOKS = ROOT / "playbooks"
GROUPS_EXPRESSION = next(task for task in load_yaml(INVENTORY) if "ansible.builtin.add_host" in task)[
    "ansible.builtin.add_host"
]["groups"]
INVENTORY_TEXT = INVENTORY.read_text(encoding="utf-8")
EVAL_GROUPS = set(re.findall(r"\['([a-z_]+_eval_group)'\]", INVENTORY_TEXT))
PREFETCH_GROUPS = set(
    next(
        play
        for play in load_yaml(PLAYBOOKS / "site.yml")
        if play.get("name") == "Pre-fetch resource-domain secrets from OpenBao"
    )["hosts"].split(":")
)
# Production groups that consume production secrets or hold production hosts for an app.
PRODUCTION_GROUPS = {"dify_group", "langflow_group", "ai_agent_pool_group", "hermes_agent_group"} | PREFETCH_GROUPS

EVAL_HOSTS = [
    pytest.param("dify-eval-fixture", ["dify-eval"], "dify_eval_group", id="dify-eval"),
    pytest.param("langflow-eval-fixture", ["langflow-eval"], "langflow_eval_group", id="langflow-eval"),
    pytest.param("sim-eval-fixture", ["sim-eval"], "sim_eval_group", id="sim-eval"),
]
PRODUCTION_HOSTS = [
    pytest.param("dify-prod-fixture", ["dify"], "dify_group", id="dify-prod"),
    pytest.param("langflow-prod-fixture", ["langflow"], "langflow_group", id="langflow-prod"),
]
DUAL_TAGGED_HOSTS = [
    pytest.param("dify-both-fixture", ["dify", "dify-eval"], "dify_eval_group", "dify_group", id="dify-both"),
    pytest.param(
        "langflow-both-fixture", ["langflow", "langflow-eval"], "langflow_eval_group", "langflow_group",
        id="langflow-both",
    ),
]


def groups_for(name: str, tags: list[str]) -> set[str]:
    variables = {
        "item": {"key": name, "value": {"tags": tags}},
        "_ai_agent_by_container": {},
        "_llm_cpu_pool_tags": ["llm-cpu-pool"],
        "_llm_cpu_moe_tag": "llm-cpu-moe",
        "_llm_cpu_9b_tag": "llm-cpu-9b",
        "load_tofu_gpu_engine_set_present": False,
        "load_tofu_gpu_engine_selector": "",
    }
    return set(render(GROUPS_EXPRESSION, variables))


def test_the_evaluation_group_set_is_exactly_the_three_known_groups() -> None:
    assert EVAL_GROUPS == {"sim_eval_group", "dify_eval_group", "langflow_eval_group"}


def test_prefetch_groups_come_from_the_site_playbook() -> None:
    assert PREFETCH_GROUPS == {"managed_secrets_group", "clickhouse_group"}


@pytest.mark.parametrize(("name", "tags", "eval_group"), EVAL_HOSTS)
def test_evaluation_host_joins_only_its_own_evaluation_group(name: str, tags: list[str], eval_group: str) -> None:
    groups = groups_for(name, tags)

    assert groups & EVAL_GROUPS == {eval_group}
    assert groups & PRODUCTION_GROUPS == set()
    assert "ai_agent_pool_group" not in groups


@pytest.mark.parametrize(("name", "tags", "production_group"), PRODUCTION_HOSTS)
def test_production_host_never_joins_an_evaluation_group(name: str, tags: list[str], production_group: str) -> None:
    groups = groups_for(name, tags)

    assert production_group in groups
    assert groups & EVAL_GROUPS == set()


@pytest.mark.parametrize(("name", "tags", "eval_group", "production_group"), DUAL_TAGGED_HOSTS)
def test_host_tagged_for_both_never_gets_the_production_group(
    name: str, tags: list[str], eval_group: str, production_group: str
) -> None:
    groups = groups_for(name, tags)

    assert eval_group in groups
    assert production_group not in groups


def test_inventory_maps_each_evaluation_tag_to_exactly_one_group() -> None:
    for tag, group in (("dify-eval", "dify_eval_group"), ("langflow-eval", "langflow_eval_group")):
        assert f"'{tag}' in (item.value.tags | default([]))" in INVENTORY_TEXT
        assert INVENTORY_TEXT.count(f"['{group}']") == 1


def _playbook_text(path: str) -> str:
    return (PLAYBOOKS / path).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "playbook",
    sorted(path.name for path in PLAYBOOKS.glob("*.yml") if path.name != "specialist-eval.yml"),
)
def test_no_other_playbook_imports_or_targets_an_evaluation_group(playbook: str) -> None:
    text = _playbook_text(playbook)

    assert "specialist-eval" not in text
    assert not any(group in text for group in EVAL_GROUPS)


def test_site_playbook_does_not_import_the_evaluation_playbook() -> None:
    site = _playbook_text("site.yml")

    assert "specialist-eval" not in site
    assert "import_playbook: specialist-eval" not in site


def _eval_plays() -> list[dict]:
    return load_yaml(PLAYBOOKS / "specialist-eval.yml")


def _is_driver_include(task: dict) -> bool:
    return task.get("ansible.builtin.include_role", {}).get("name") == "dryvist.homelab.openbao_secrets"


def _pins_endpoint(play: dict) -> bool:
    return any(_is_driver_include(task) for task in play.get("tasks", []))


def _eval_target_plays() -> list[dict]:
    return [play for play in _eval_plays() if play.get("hosts") in EVAL_GROUPS]


def test_specialist_eval_loads_the_inventory_before_any_target_play() -> None:
    plays = _eval_plays()

    assert plays[0].get("import_playbook") == "../inventory/load_tofu.yml"
    assert all("import_playbook" not in play for play in plays[1:])


def test_specialist_eval_targets_only_evaluation_groups() -> None:
    plays = _eval_plays()
    others = [play for play in plays if "hosts" in play and not _pins_endpoint(play)]

    assert {play["hosts"] for play in _eval_target_plays()} == {
        "sim_eval_group",
        "dify_eval_group",
        "langflow_eval_group",
    }
    assert all(play["hosts"] in EVAL_GROUPS for play in others)


def test_the_openbao_endpoint_is_pinned_after_the_inventory_and_before_every_evaluation_play() -> None:
    plays = _eval_plays()
    loader = next(i for i, play in enumerate(plays) if play.get("import_playbook") == "../inventory/load_tofu.yml")
    pinned = [i for i, play in enumerate(plays) if _pins_endpoint(play)]
    first_evaluation = min(i for i, play in enumerate(plays) if play.get("hosts") in EVAL_GROUPS)

    assert len(pinned) == 1
    assert loader < pinned[0] < first_evaluation


def test_the_endpoint_play_reads_no_domain_and_targets_no_group() -> None:
    play = next(play for play in _eval_plays() if _pins_endpoint(play))
    include = next(task for task in play["tasks"] if _is_driver_include(task))

    assert play["hosts"] == "localhost"
    assert include["vars"]["openbao_secrets_domains"] == []
    assert include["vars"]["openbao_secrets_fail_when_unavailable"] is True


def test_the_endpoint_comes_from_the_same_driver_site_yml_uses() -> None:
    assert "dryvist.homelab.openbao_secrets" in _playbook_text("site.yml")
    assert "dryvist.homelab.openbao_secrets" in _playbook_text("specialist-eval.yml")


@pytest.mark.parametrize("group", ["dify_eval_group", "langflow_eval_group"])
def test_each_evaluation_app_has_its_group_vars_file(group: str) -> None:
    assert (GROUP_VARS / f"{group}.yml").is_file()


@pytest.mark.parametrize("play", _eval_target_plays(), ids=lambda play: play["hosts"])
def test_each_evaluation_play_is_one_isolated_block_with_its_role(play: dict) -> None:
    assert "specialist-eval" in play["tags"]
    block = play["tasks"][0]
    role = block["block"][-1]["ansible.builtin.include_role"]["name"]

    assert "block" in block and "rescue" in block
    assert role in play["tags"]
    assert block["rescue"][-1]["vars"]["_isolated_play_name"] == role
