"""The router projects pinned LiteLLM price metadata without registry prices."""

from __future__ import annotations

import json
import re
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "roles/llm_router/templates"
DEFAULTS_FILE = REPO_ROOT / "roles/llm_router/defaults/main/66-price-map-rates.yml"
KEY_SCOPE_DEFAULTS_FILE = REPO_ROOT / "roles/llm_router/defaults/main/59-key-scopes.yml"
TASK_FILE = REPO_ROOT / "roles/llm_router/tasks/price-map-rates.yml"
FIXTURE_FILE = REPO_ROOT / "tests/llm_router/fixtures/model_prices_needed_rows.json"
REGISTRY_FILES = (
    REPO_ROOT / "llm-models.d/40-hermes-cloud.yml",
    REPO_ROOT / "llm-models.d/50-openrouter.yml",
)


def _defaults() -> dict:
    return yaml.safe_load(DEFAULTS_FILE.read_text(encoding="utf-8"))


def _fixture() -> dict:
    return json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))


def _registry_entries() -> list[dict]:
    values = _defaults()
    chatgpt_defaults = yaml.safe_load(
        (REPO_ROOT / "roles/llm_router/defaults/main/34-chatgpt.yml").read_text(encoding="utf-8")
    )
    entries = []
    for path in REGISTRY_FILES:
        registry = yaml.safe_load(path.read_text(encoding="utf-8"))
        rows = next(value for value in registry.values() if isinstance(value, list))
        for entry in rows:
            if not entry.get("enabled", False):
                continue
            model = entry.get("litellm_model_name") or f"{entry['provider']}/{entry['upstream_model_id']}"
            for variable in ("llm_router_chatgpt_model", "llm_router_ci_luna_model"):
                model = model.replace("{{ " + variable + " }}", str(chatgpt_defaults[variable]))
            entries.append({**entry, "_resolved_model": model})
    return entries


def _candidate_keys(model: str, provider: str) -> list[str]:
    candidates = [model]
    if "/" in model and model.split("/", 1)[0] == provider:
        candidates.append(model.split("/", 1)[1])
    return candidates


def _resolve_rates(price_map: dict, entries: list[dict]) -> tuple[dict, dict]:
    rates = {}
    keys_by_entry = {}
    for entry in entries:
        for key in _candidate_keys(entry["_resolved_model"], entry["provider"]):
            row = price_map.get(key)
            if not isinstance(row, dict):
                continue
            input_rate = row.get("input_cost_per_token")
            output_rate = row.get("output_cost_per_token")
            if (
                isinstance(input_rate, (int, float))
                and not isinstance(input_rate, bool)
                and isinstance(output_rate, (int, float))
                and not isinstance(output_rate, bool)
            ):
                keys_by_entry[entry["client_model_id"]] = key
                if input_rate > 0 and output_rate > 0:
                    rates[entry["client_model_id"]] = {"input": input_rate, "output": output_rate}
                break
    return rates, keys_by_entry


def _render_model_list(entries: list[dict], rates: dict) -> list[dict]:
    local_registry_tiers = yaml.safe_load(KEY_SCOPE_DEFAULTS_FILE.read_text(encoding="utf-8"))[
        "llm_router_local_registry_tiers"
    ]
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
        trim_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["to_json"] = json.dumps
    env.filters["bool"] = bool
    env.globals.update(
        llm_router_deployment_tags_by_entry={},
        llm_router_deployment_tags_by_model={},
    )
    model_name = lambda model: model.get("litellm_model_name") or f"{model['provider']}/{model['upstream_model_id']}"
    env.globals["litellm_model"] = model_name
    tags = lambda model, class_override="": json.dumps(
        ([class_override] if class_override else []) + ["retains", "sensitivity:public"]
    )
    env.globals["deployment_tags"] = tags

    cloud_entries = []
    openrouter_entries = []
    for entry in entries:
        rendered = {
            **entry,
            "litellm_model_name": entry["_resolved_model"],
            "context_window": 131072,
            "max_output_tokens": 8192,
            "_model": entry["_resolved_model"],
        }
        if entry["tier"] == "hermes-cloud":
            cloud_entries.append(rendered)
        elif entry["tier"] == "openrouter":
            openrouter_entries.append(rendered)

    empty_lists = {
        "llm_router_large_models": [],
        "llm_router_opencode_models": [],
        "llm_router_vllm_models": [],
        "llm_router_hermes_local_models": [],
        "llm_router_light_models": [],
        "llm_router_cpu_moe_models": [],
        "llm_router_cpu_9b_models": [],
        "llm_router_gpu_models": [],
        "llm_router_direct_gpu_models": [],
        "llm_router_direct_studio_models": [],
    }
    rendered = env.get_template("model-list.yaml.j2").render(
        **empty_lists,
        llm_router_large_passthrough_enabled=False,
        llm_router_hermes_agents={},
        llm_router_hermes_agent_identities={},
        llm_router_hermes_identity_bearers={},
        llm_router_hermes_agent_timeout=1,
        llm_router_hermes_agent_context_window=1,
        _llm_router_m_hermes_cloud_models_credentialed=cloud_entries,
        llm_router_hermes_cloud_rpm=12,
        llm_router_hermes_cloud_tpm=500000,
        llm_router_hermes_cloud_max_parallel_requests=2,
        llm_router_hermes_cloud_monthly_budget_period="1mo",
        llm_router_multi_member_cloud_groups=["hermes-cloud-openrouter"],
        llm_router_cloud_group_retries=1,
        llm_router_request_timeout_seconds=2400,
        llm_router_openrouter_models=openrouter_entries,
        llm_router_openrouter_default_order=70,
        llm_router_openrouter_rpm=60,
        llm_router_openrouter_tpm=200000,
        llm_router_openrouter_max_parallel_requests=4,
        llm_router_local_registry_tiers=local_registry_tiers,
        _llm_router_m_resolved_credentials={
            "OPENROUTER_API_KEY": "fixture",
            "OPENROUTER_HERMES_KEY": "fixture",
            "GEMINI_API_KEY": "fixture",
            "DASHSCOPE_API_KEY": "fixture",
            "CHATGPT_LOGIN_FILE": "fixture",
            "OPENAI_API_KEY": "fixture",
        },
        llm_router_price_rates=rates,
        llm_router_deployment_tags_by_entry={},
        llm_router_deployment_tags_by_model={},
        llm_router_llm_vllm_enabled=False,
        llm_router_llm_fast_enabled=False,
        llm_router_cpu_pool_backend_urls_by_tier={},
        llm_router_local_num_retries=0,
        llm_router_local_stream_timeout_seconds=1,
        llm_router_local_request_timeout_seconds=1,
        llm_router_local_max_parallel_requests=1,
        llm_router_vllm_first_byte_timeout_seconds=1,
        llm_router_llm_large_base_url="https://fixture.invalid/v1",
        llm_router_llm_light_base_url="https://fixture.invalid/v1",
        llm_router_llm_vllm_base_url="https://fixture.invalid/v1",
        llm_router_llm_gpu_base_url="https://fixture.invalid/v1",
        llm_router_light_api_key="fixture",
        _llm_router_vllm_max_input_tokens_by_model={},
    )
    return yaml.safe_load(rendered)["model_list"]


def test_pinned_fixture_contains_only_current_rate_map_rows_needed_by_registry():
    entries = _registry_entries()
    fixture = _fixture()
    rates, keys_by_entry = _resolve_rates(fixture, entries)
    assert len(fixture) == 11
    assert set(fixture) == set(keys_by_entry.values())
    assert not any(entry["provider"] == "dashscope" and entry["client_model_id"] in rates for entry in entries)
    assert all(rate["input"] > 0 and rate["output"] > 0 for rate in rates.values())


def test_registered_gemini_openrouter_and_openai_routes_render_fixture_rates():
    entries = _registry_entries()
    fixture = _fixture()
    rates, keys_by_entry = _resolve_rates(fixture, entries)
    model_list = _render_model_list(entries, rates)

    for entry in entries:
        entry_id = entry["client_model_id"]
        if entry_id not in rates:
            continue
        model = entry["_resolved_model"]
        model_name = entry.get("route_group", entry_id)
        deployments = [
            row
            for row in model_list
            if row["model_name"] == model_name and row["litellm_params"]["model"] == model
        ]
        assert deployments, entry_id
        source = fixture[keys_by_entry[entry_id]]
        for deployment in deployments:
            params = deployment["litellm_params"]
            assert params["input_cost_per_token"] == source["input_cost_per_token"]
            assert params["output_cost_per_token"] == source["output_cost_per_token"]
            assert isinstance(params["output_cost_per_token"], float)


def test_unmatched_and_zero_rate_rows_render_no_cost_fields():
    entries = _registry_entries()
    fixture = _fixture()
    rates, keys_by_entry = _resolve_rates(fixture, entries)
    model_list = _render_model_list(entries, rates)
    no_rate_ids = {
        entry["client_model_id"]
        for entry in entries
        if entry["provider"] == "dashscope"
        or (
            entry["client_model_id"] in keys_by_entry
            and (
                fixture[keys_by_entry[entry["client_model_id"]]]["input_cost_per_token"] == 0
                or fixture[keys_by_entry[entry["client_model_id"]]]["output_cost_per_token"] == 0
            )
        )
    }

    for entry in entries:
        if entry["client_model_id"] not in no_rate_ids:
            continue
        model_name = entry.get("route_group", entry["client_model_id"])
        deployments = [
            row
            for row in model_list
            if row["model_name"] == model_name
            and row["litellm_params"]["model"] == entry["_resolved_model"]
        ]
        assert deployments
        assert all(
            "input_cost_per_token" not in row["litellm_params"]
            and "output_cost_per_token" not in row["litellm_params"]
            for row in deployments
        )


def test_price_map_resolution_tries_full_then_only_its_provider_prefix_stripped_key():
    entries = _registry_entries()
    openrouter_model = next(entry["_resolved_model"] for entry in entries if entry["provider"] == "openrouter")
    gemini_model = next(entry["_resolved_model"] for entry in entries if entry["provider"] == "gemini")
    assert _candidate_keys(openrouter_model, "openrouter") == [
        openrouter_model,
        openrouter_model.split("/", 1)[1],
    ]
    assert _candidate_keys(gemini_model, "gemini") == [
        gemini_model,
        gemini_model.split("/", 1)[1],
    ]
    assert _candidate_keys(openrouter_model, "gemini") == [openrouter_model]


def test_fetched_price_map_validation_rejects_fewer_than_minimum_keys():
    expression = _defaults()["llm_router_price_map_rates_document_valid_when"]
    validate = jinja2.Environment().compile_expression(expression)

    def valid(document: dict, override: str = "") -> bool:
        return bool(
            validate(
                hostvars={"localhost": {"_llm_router_price_map_rates_document": document}},
                llm_router_price_map_rates_file=override,
            )
        )

    assert not valid({str(index): {} for index in range(1000)})
    assert valid({str(index): {} for index in range(1001)})
    assert valid(_fixture(), "fixture.json")


def test_default_url_uses_the_version_from_the_litellm_package_pin():
    packages = [
        line
        for line in (REPO_ROOT / "roles/llm_router/files/requirements.in")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    ]
    environment = jinja2.Environment()
    environment.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
    environment.filters["regex_escape"] = re.escape
    environment.filters["split"] = lambda value, separator: value.split(separator)
    environment.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(
        pattern, replacement, value
    )
    url = environment.from_string(_defaults()["llm_router_price_map_rates_url"]).render(
        llm_router_pip_packages=packages
    )
    version = next(
        package.split("==", 1)[1]
        for package in packages
        if package.startswith("litellm[proxy]==")
    )
    assert url.endswith(f"/v{version}/model_prices_and_context_window.json")


def test_task_keeps_fetch_failures_loud_and_shares_controller_facts():
    task = TASK_FILE.read_text(encoding="utf-8")
    assert "return_content: true" in task
    assert "retries: 3" in task
    assert "delay: 2" in task
    assert "run_once: true" in task
    assert "delegate_to: localhost" in task
    assert "delegate_facts: true" in task
    assert "llm_router_price_map_rates_document_valid_when" in task
    assert "llm_router_price_rates" in task
    assert "model.split('/', 1)[0] == price_model.provider" in task
    assert "model.split('/', 1)[1]" in task
