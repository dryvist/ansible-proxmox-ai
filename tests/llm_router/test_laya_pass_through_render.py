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
        "hostvars": {"classifier": {"laya_systemone_port": 1234}},
        "_llm_router_laya_defaults": {"laya_systemone_port": 5678},
        "llm_router_apex_domain": "domain",
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
