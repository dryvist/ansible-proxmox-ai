"""Local admission sends a bounded completion to the listed model's lane."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_load_and_serve_request_is_bounded_and_uses_the_listing_target():
    tasks = yaml.safe_load(
        (ROOT / "roles/llm_router/tasks/verify-servable.yml").read_text()
    )
    defaults = yaml.safe_load(
        (ROOT / "roles/llm_router/defaults/main/52-probe.yml").read_text()
    )
    completion = next(
        task for task in tasks
        if task.get("register") == "_llm_router_completion_precheck_responses"
    )
    request = completion["ansible.builtin.uri"]
    assert request["method"] == "POST"
    assert request["url"] == "{{ item.endpoint | regex_replace('/models$', '/chat/completions') }}"
    assert request["body"]["model"] == "{{ item.model }}"
    assert request["body"]["stream"] is False
    assert request["body"]["max_tokens"] == "{{ llm_router_probe_completion_tokens }}"
    assert defaults["llm_router_probe_completion_tokens"] == 1
    assert "Authorization" in request["headers"]
    assert "llm_router_llm_large_bearer" in request["headers"]
    assert completion["when"] == "item.model in _llm_router_precheck_listed_models"
    assert completion["no_log"] is True
    assert completion["check_mode"] is False
