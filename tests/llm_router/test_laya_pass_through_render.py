"""The optional classifier route keeps its upstream bearer out of config."""

import json
from pathlib import Path

import jinja2
import yaml


TEMPLATE = Path(__file__).resolve().parents[2] / "roles/llm_router/templates/general-settings.yaml.j2"


def test_classifier_pass_through_render():
    env = jinja2.Environment()
    env.filters["bool"] = bool
    env.filters["to_json"] = json.dumps
    template = env.from_string(TEMPLATE.read_text())
    context = {
        "groups": {"llm_classify": ["classifier"]},
        "llm_router_classifier_url": "http://classifier.domain:1234/v1/systemone",
        "llm_router_enforce_fallback_model_access": True,
        "llm_router_background_health_checks": False,
    }
    settings = yaml.safe_load(template.render(**context))["general_settings"]
    assert settings["pass_through_endpoints"] == [{
        "path": "/v1/systemone",
        "target": "http://" + "classifier" + "." + "domain" + ":1234/v1/systemone",
        "headers": {"Authorization": "Bearer os.environ/LLM_LAYA_API_KEY"},
        "auth": True,
    }]
    context["groups"] = {}
    assert "pass_through_endpoints" not in yaml.safe_load(template.render(**context))["general_settings"]


def test_zcode_usage_pass_through_render():
    env = jinja2.Environment()
    env.filters["bool"] = bool
    env.filters["to_json"] = json.dumps
    template = env.from_string(TEMPLATE.read_text())
    base = {
        "llm_router_enforce_fallback_model_access": True,
        "llm_router_background_health_checks": False,
        "llm_router_zcode_usage_api_key": "render-test-value",
        "llm_router_zcode_usage_path": "/v1/usage/zcode",
        "llm_router_zcode_usage_url": "https://api.z.ai/api/monitor/usage/quota/limit",
    }
    # Usage route alone: no llm_classify hosts, so only the plan-usage entry renders.
    settings = yaml.safe_load(template.render(groups={}, **base))["general_settings"]
    assert settings["pass_through_endpoints"] == [{
        "path": "/v1/usage/zcode",
        "target": "https://api.z.ai/api/monitor/usage/quota/limit",
        "headers": {"Authorization": "Bearer os.environ/LLM_ZCODE_USAGE_API_KEY"},
        "auth": True,
    }]
    # Both routes active: classifier first.
    context = {
        "groups": {"llm_classify": ["classifier"]},
        "llm_router_classifier_url": "http://classifier.domain:1234/v1/systemone",
        **base,
    }
    settings = yaml.safe_load(template.render(**context))["general_settings"]
    assert settings["pass_through_endpoints"] == [
        {
            "path": "/v1/systemone",
            "target": "http://classifier.domain:1234/v1/systemone",
            "headers": {"Authorization": "Bearer os.environ/LLM_LAYA_API_KEY"},
            "auth": True,
        },
        {
            "path": "/v1/usage/zcode",
            "target": "https://api.z.ai/api/monitor/usage/quota/limit",
            "headers": {"Authorization": "Bearer os.environ/LLM_ZCODE_USAGE_API_KEY"},
            "auth": True,
        },
    ]
    # Neither route active (no llm_router_zcode_usage_* vars at all, as today):
    # the key is absent.
    assert "pass_through_endpoints" not in yaml.safe_load(template.render(
        groups={},
        llm_router_enforce_fallback_model_access=True,
        llm_router_background_health_checks=False,
    ))["general_settings"]
