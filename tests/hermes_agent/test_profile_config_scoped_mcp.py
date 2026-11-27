"""A regular profile's config renders its shared settings and only its own MCP servers.

Split out of test_profile_contract.py (over the file token budget).
"""
from __future__ import annotations

import yaml

from _cron_pool_ceiling_shared import run_budget_seconds
from test_profile_contract import _RENDER_CONTEXT, ROLE_ROOT, _defaults, _jinja_env, _regular_profiles


def test_profile_config_template_renders_scoped_mcp_only() -> None:
    env = _jinja_env()
    src = (ROLE_ROOT / "templates" / "config-profile.yaml.j2").read_text()
    defaults = _defaults()
    for profile in _regular_profiles():
        context = dict(_RENDER_CONTEXT)
        context["hermes_agent_langfuse_enabled"] = True
        context["hermes_agent_plugins_enabled"] = ["dryvist-guardrails", "hindsight", "langfuse"]
        context["hermes_agent_disabled_toolsets"] = defaults["hermes_agent_disabled_toolsets"]
        context["hermes_agent_cron_toolsets"] = defaults["hermes_agent_api_server_toolsets"]
        context["hermes_agent_context_limits"] = {}
        rendered = env.from_string(src).render(hermes_agent_profile=profile, **context)
        parsed = yaml.safe_load(rendered)

        assert parsed["model"]["provider"] == defaults["hermes_agent_model_shared_provider"]
        assert parsed["plugins"]["enabled"] == ["dryvist-guardrails", "hindsight", "langfuse"]
        assert parsed["kanban"] == {"dispatch_in_gateway": False}
        assert parsed["agent"]["run_budget_seconds"] == run_budget_seconds()
        assert "dashboard" not in parsed
        assert "platforms" not in parsed
        assert ("platform_toolsets" in parsed) == ("toolsets" in profile)
        # Goal-mode judging must be wired identically to the default profile,
        # or completion judging fails for any card this profile owns.
        assert "auxiliary" in parsed
        assert (
            parsed["auxiliary"]["goal_judge"]["provider"]
            == defaults["hermes_agent_model_shared_provider"]
        )

        rendered_servers = set(parsed.get("mcp_servers", {}))
        assert rendered_servers == set(profile["mcp"])
