"""Contracts for the LiteLLM converge reports."""

from __future__ import annotations

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from test_price_map_reload import ROLE_ROOT, _debug_task_is_safe


def test_model_info_report_is_read_only_and_whitelists_aggregates():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    model_read = next(
        task for task in tasks if task.get("name") == "Read this router's model info rows"
    )
    model_report = next(
        task for task in tasks if task.get("name") == "Report this router's model rows by provider and price"
    )
    uri = model_read["ansible.builtin.uri"]
    message = model_report["ansible.builtin.debug"]["msg"]

    assert uri["url"].endswith("/model/info")
    assert uri["headers"]["Authorization"] == "Bearer {{ llm_router_master_key }}"
    assert uri["status_code"] == 200
    assert uri["timeout"] == "{{ llm_router_seed_request_timeout }}"
    assert model_read["register"] == "llm_router_model_info_status"
    assert model_read["changed_when"] is False
    assert model_read["failed_when"] is False
    assert model_read["check_mode"] is False
    assert model_read["no_log"] is True
    assert set(message) == {
        "model_rows",
        "hosted_vllm_provider_rows",
        "hosted_vllm_prefix_rows",
        "priced_rows",
        "hermes_cloud_rows",
        "hermes_cloud_priced_rows",
    }
    assert _debug_task_is_safe(yaml.safe_dump(model_report))
    assert all(".json.data" in value for value in message.values())
    assert all("{%" not in value for value in message.values())


def test_model_info_report_renders_with_the_real_templar():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    message = next(
        task for task in tasks if task.get("name") == "Report this router's model rows by provider and price"
    )["ansible.builtin.debug"]["msg"]
    cloud_route = "route" + "-cloud-fixture"
    hermes_cloud_models = [{"route_group": cloud_route}]

    def render(status: dict) -> dict:
        templar = Templar(loader=DataLoader())
        templar.available_variables = {
            "llm_router_model_info_status": status,
            "llm_router_hermes_cloud_models_credentialed": hermes_cloud_models,
        }
        return {key: templar.template(trust_as_template(value)) for key, value in message.items()}

    rows = [
        {
            "model_name": "route" + "-gpu-fixture",
            "litellm_params": {"model": "fixture"},
            "model_info": {"litellm_provider": "hosted_vllm", "input_cost_per_token": 0.0},
        },
        {
            "model_name": "route" + "-second-gpu-fixture",
            "litellm_params": {"model": "hosted_vllm/fixture"},
            "model_info": {"litellm_provider": "test-provider", "input_cost_per_token": None},
        },
        {
            "model_name": cloud_route,
            "litellm_params": {"model": "fixture-provider/fixture"},
            "model_info": {"litellm_provider": "fixture-provider", "input_cost_per_token": 0.000001},
        },
    ]
    assert render({"status": 200, "json": {"data": rows}}) == {
        "model_rows": "3",
        "hosted_vllm_provider_rows": "1",
        "hosted_vllm_prefix_rows": "1",
        "priced_rows": "1",
        "hermes_cloud_rows": "1",
        "hermes_cloud_priced_rows": "1",
    }
    assert render({"status": -1}) == {
        "model_rows": "absent",
        "hosted_vllm_provider_rows": "absent",
        "hosted_vllm_prefix_rows": "absent",
        "priced_rows": "absent",
        "hermes_cloud_rows": "absent",
        "hermes_cloud_priced_rows": "absent",
    }
    assert set(render({"status": 200, "json": {"data": []}}).values()) == {"absent"}


def test_key_alias_report_is_read_only_and_whitelists_alias_names():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/seed-consumers.yml").read_text(encoding="utf-8"))
    alias_read = next(task for task in tasks if task.get("name") == "Read the router's live key alias listing")
    alias_report = next(task for task in tasks if task.get("name") == "Report declared and live key aliases")
    uri = alias_read["ansible.builtin.uri"]
    message = alias_report["ansible.builtin.debug"]["msg"]

    assert uri["url"].endswith("/key/aliases?page=1&size=100")
    assert uri.get("method", "GET") == "GET"
    assert uri["headers"]["Authorization"] == "Bearer {{ llm_router_master_key }}"
    assert uri["status_code"] == 200
    assert uri["timeout"] == "{{ llm_router_seed_request_timeout }}"
    assert alias_read["register"] == "llm_router_key_aliases_status"
    assert alias_read["changed_when"] is False
    assert alias_read["failed_when"] is False
    assert alias_read["check_mode"] is False
    assert alias_read["no_log"] is True
    assert set(message) == {
        "declared_key_aliases",
        "live_key_aliases",
        "live_undeclared_aliases",
        "declared_missing_aliases",
        "listing_truncated",
    }
    assert "llm_router_virtual_keys" in message["declared_key_aliases"]
    assert _debug_task_is_safe(yaml.safe_dump(alias_report))
    assert all("{%" not in value for value in message.values())


def test_key_alias_report_renders_with_the_real_templar():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/seed-consumers.yml").read_text(encoding="utf-8"))
    message = next(task for task in tasks if task.get("name") == "Report declared and live key aliases")[
        "ansible.builtin.debug"
    ]["msg"]
    declared_alias = "declared" + "-fixture"
    missing_alias = "missing" + "-fixture"
    undeclared_alias = "live" + "-only-fixture"
    virtual_keys = [
        {"alias": declared_alias, "value": True},
        {"alias": missing_alias, "value": True},
    ]

    def render(status: dict) -> dict:
        templar = Templar(loader=DataLoader())
        templar.available_variables = {
            "llm_router_key_aliases_status": status,
            "llm_router_virtual_keys": virtual_keys,
        }
        return {key: templar.template(trust_as_template(value)) for key, value in message.items()}

    assert render(
        {
            "status": 200,
            "json": {
                "aliases": [declared_alias, undeclared_alias],
                "total_count": 2,
                "current_page": 1,
                "total_pages": 1,
                "size": 100,
            },
        }
    ) == {
        "declared_key_aliases": "2",
        "live_key_aliases": "2",
        "live_undeclared_aliases": f'["{undeclared_alias}"]',
        "declared_missing_aliases": "1",
        "listing_truncated": "false",
    }
    assert render({"status": -1}) == {
        "declared_key_aliases": "2",
        "live_key_aliases": "absent",
        "live_undeclared_aliases": "absent",
        "declared_missing_aliases": "absent",
        "listing_truncated": "absent",
    }
    truncated = render(
        {
            "status": 200,
            "json": {
                "aliases": [undeclared_alias],
                "total_count": 101,
                "current_page": 1,
                "total_pages": 2,
                "size": 100,
            },
        }
    )
    assert truncated["live_undeclared_aliases"] == f'["{undeclared_alias}"]'
    assert truncated["declared_missing_aliases"] == "absent"
    assert truncated["listing_truncated"] == "true"
    malformed = render(
        {
            "status": 200,
            "json": {
                "aliases": [{"alias": undeclared_alias}],
                "total_count": 1,
                "current_page": 1,
                "total_pages": 1,
                "size": 100,
            },
        }
    )
    assert malformed["live_key_aliases"] == "absent"
    assert malformed["live_undeclared_aliases"] == "absent"
    assert malformed["declared_missing_aliases"] == "absent"
