"""Catalog import in named slices."""

import json

from jinja2 import Environment

from _agent_dsl_shared import IMPORT_LOOP, _task, _yaml
from _defaults import load_defaults

BUILD = "Build agent DSL URLs from every catalog manifest entry"
CHECK = "Fail when a selected catalog entry name is not in the manifest"
MAIN = IMPORT_LOOP.with_name("main.yml")
WIRE = "Wire the tier2 rotation to each agent's webhook trigger"


def _render(expression, **variables):
    environment = Environment()
    environment.filters["from_json"] = json.loads
    environment.filters["difference"] = lambda left, right: [item for item in left if item not in right]
    return environment.from_string("{{ " + expression + " }}").render(**variables)


def test_an_empty_select_imports_every_entry_and_a_list_imports_only_its_names():
    condition = _task(_yaml(IMPORT_LOOP), BUILD)["when"][-1]

    def selected(select, name):
        return _render(
            condition,
            dify_docker_agent_dsl_select=select,
            dify_docker_agent_dsl_manifest_entry={"name": name},
        ) == "True"

    assert load_defaults()["dify_docker_agent_dsl_select"] == []
    assert selected([], "tier2-rotation")
    assert selected(["ai-news"], "ai-news")
    assert not selected(["ai-news"], "tier2-rotation")


def test_a_selected_name_missing_from_the_manifest_fails_before_any_fetch():
    tasks = _yaml(IMPORT_LOOP)
    names = [task.get("name") for task in tasks]
    check = _task(tasks, CHECK)
    manifest = json.dumps([{"name": "ai-news"}, {"name": "tier2-rotation"}])

    def passes(select):
        return _render(
            check["ansible.builtin.assert"]["that"][0],
            dify_docker_agent_dsl_select=select,
            dify_docker_agent_dsl_manifest_content=manifest,
        ) == "True"

    assert passes(["ai-news"])
    assert not passes(["ai-news", "ai-nwes"])
    assert "dify_docker_agent_dsl_select | length > 0" in check["when"]
    assert names.index(BUILD) < names.index(CHECK) < names.index(
        "Fetch and verify all catalog agent DSLs before importing any"
    )


def test_a_sliced_import_wires_the_rotation_only_with_the_rotation_app():
    condition = _task(_yaml(MAIN), WIRE)["when"][-1]
    rotation = load_defaults()["dify_docker_rotation_app_name"]

    def wires(select):
        return _render(
            condition,
            dify_docker_agent_dsl_select=select,
            dify_docker_rotation_app_name=rotation,
        ) == "True"

    assert wires([])
    assert wires(["zammad-triage", rotation])
    assert not wires(["code-simplifier", "commit-grouper"])
