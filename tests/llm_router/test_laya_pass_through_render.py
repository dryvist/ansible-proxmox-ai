"""The optional classifier route keeps its upstream bearer out of config."""

import json
from pathlib import Path

import jinja2
import yaml


TEMPLATE = Path(__file__).resolve().parents[2] / "roles/llm_router/templates/general-settings.yaml.j2"

# The agent-quota route renders only with its bearer set; these contexts set it.
QUOTA_CONTEXT = {
    "llm_router_agent_quota_path": "/agent-quota",
    "llm_router_agent_quota_upstream_base_url": "https://vmauth.example",
    "llm_router_agent_quota_upstream_path": "/agent-quota",
    "llm_router_agent_quota_reader_token_env": "AGENT_QUOTA_READER_TOKEN",
    "llm_router_agent_quota_reader_token": "reader-placeholder",
}

QUOTA_ENDPOINT = {
    "path": "/agent-quota",
    "target": "https://vmauth.example/agent-quota",
    "headers": {"Authorization": "Bearer os.environ/AGENT_QUOTA_READER_TOKEN"},
    "auth": True,
    "methods": ["GET"],
}


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
        **QUOTA_CONTEXT,
    }
    text = template.render(**context)
    assert "query=" not in text
    settings = yaml.safe_load(text)["general_settings"]
    assert settings["pass_through_endpoints"] == [QUOTA_ENDPOINT, {
        "path": "/v1/systemone",
        "target": "http://" + "classifier" + "." + "domain" + ":1234/v1/systemone",
        "headers": {"Authorization": "Bearer os.environ/LLM_LAYA_API_KEY"},
        "auth": True,
    }]
    context["groups"] = {}
    text = template.render(**context)
    assert "query=" not in text
    # No optional route active: only the core agent-quota entry remains.
    assert yaml.safe_load(text)["general_settings"]["pass_through_endpoints"] == [QUOTA_ENDPOINT]


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
        **QUOTA_CONTEXT,
    }
    # Usage route alone: no llm_classify hosts, so only the plan-usage entry
    # renders besides the core agent-quota route.
    text = template.render(groups={}, **base)
    assert "query=" not in text
    settings = yaml.safe_load(text)["general_settings"]
    assert settings["pass_through_endpoints"] == [QUOTA_ENDPOINT, {
        "path": "/v1/usage/zcode",
        "target": "https://api.z.ai/api/monitor/usage/quota/limit",
        "headers": {"Authorization": "Bearer os.environ/LLM_ZCODE_USAGE_API_KEY"},
        "auth": True,
    }]
    # Both optional routes active: quota first, then classifier, then zcode.
    context = {
        "groups": {"llm_classify": ["classifier"]},
        "llm_router_classifier_url": "http://classifier.domain:1234/v1/systemone",
        **base,
    }
    text = template.render(**context)
    assert "query=" not in text
    settings = yaml.safe_load(text)["general_settings"]
    assert settings["pass_through_endpoints"] == [
        QUOTA_ENDPOINT,
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
    # Neither optional route active (no llm_router_zcode_usage_* vars at all,
    # as today): only the agent-quota entry renders.
    text = template.render(
        groups={},
        llm_router_enforce_fallback_model_access=True,
        llm_router_background_health_checks=False,
        **QUOTA_CONTEXT,
    )
    assert "query=" not in text
    assert yaml.safe_load(text)["general_settings"]["pass_through_endpoints"] == [QUOTA_ENDPOINT]


def test_agent_quota_route_needs_its_bearer():
    env = jinja2.Environment()
    env.filters["bool"] = bool
    env.filters["to_json"] = json.dumps
    template = env.from_string(TEMPLATE.read_text())
    base = {
        "llm_router_enforce_fallback_model_access": True,
        "llm_router_background_health_checks": False,
        **QUOTA_CONTEXT,
        "llm_router_agent_quota_reader_token": "",
    }
    # No bearer and no other route: no pass-through key, so no header names an unset env var.
    text = template.render(groups={}, **base)
    assert "AGENT_QUOTA_READER_TOKEN" not in text
    assert "pass_through_endpoints" not in yaml.safe_load(text)["general_settings"]
    # No bearer beside an active route: that route renders alone.
    text = template.render(
        groups={"llm_classify": ["classifier"]},
        llm_router_classifier_url="http://classifier.domain:1234/v1/systemone",
        **base,
    )
    assert "AGENT_QUOTA_READER_TOKEN" not in text
    paths = [e["path"] for e in yaml.safe_load(text)["general_settings"]["pass_through_endpoints"]]
    assert paths == ["/v1/systemone"]
