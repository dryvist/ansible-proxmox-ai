from __future__ import annotations

from pathlib import Path

import yaml

from _role_files import role_defaults
from test_context_limits import ROLE_ROOT, _render, _sample_limits


FIXTURE = Path(__file__).parent / "fixtures" / "hermes-v2026.9.11-auxiliary-keys.yml"
TEMPLATES = ("config.yaml.j2", "config-profile.yaml.j2")


def test_auxiliary_tasks_are_pinned_to_the_router_in_both_configs() -> None:
    fixture = yaml.safe_load(FIXTURE.read_text())
    assert fixture["version"] == "v2026.9.11"
    expected_keys = set(fixture["auxiliary_task_keys"])

    for template in TEMPLATES:
        _, config = _render(template, _sample_limits())
        auxiliary = config["auxiliary"]
        assert set(auxiliary) - {"transient_retries"} == expected_keys, template

        model = config["model"]
        assert auxiliary["transient_retries"] == 1
        assert auxiliary["goal_judge"] == {
            "provider": model["provider"],
            "model": "judge",
            "base_url": model["base_url"],
            "api_key": "${HERMES_AGENT_MODEL_API_KEY}",
            "api_mode": model["api_mode"],
        }

        for task in expected_keys - {"goal_judge"}:
            route = auxiliary[task]
            assert route["provider"] == "custom", (template, task)
            assert route["provider"] != "auto", (template, task)
            assert route["model"] == model["default"], (template, task)
            assert route["base_url"] == model["base_url"], (template, task)
            assert route["api_key"] == "${HERMES_AGENT_MODEL_API_KEY}", (template, task)
            assert route["api_mode"] == model["api_mode"], (template, task)
            for key in ("timeout", "extra_body", "fallback_chain"):
                assert key not in route, (template, task, key)

    defaults = role_defaults(ROLE_ROOT)
    assert defaults["hermes_agent_auxiliary_tasks"] == [
        key for key in fixture["auxiliary_task_keys"] if key != "goal_judge"
    ]
