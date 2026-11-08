"""Slack agents fall back to their own platform-env token fields. Runs bare or under pytest."""
from pathlib import Path

import jinja2
import yaml

TASKS = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent" / "tasks" / "slack_credentials.yml"


def _task(name: str) -> dict:
    return next(t for t in yaml.safe_load(TASKS.read_text()) if t["name"] == name)


def test_bot_token_fallback_reads_the_agent_specific_field_for_both_agents():
    task = _task("Read the platform bot-token fallback when the agent token is empty")
    lookup = task["ansible.builtin.set_fact"]["hermes_agent_slack_bot_token"]
    assert "'secret/data/platform/ansible/env:SLACK_' ~ (hermes_agent_id | upper) ~ '_BOT_TOKEN'" in lookup
    assert "hermes_agent_id in ['hermes', 'donna']" in task["when"]
    assert task["no_log"] is True


def test_app_token_platform_field_is_a_fallback_for_donna_only():
    when = jinja2.Environment().compile_expression(_task("Read the Hermes Slack app credential")["when"])
    assert when(hermes_agent_id="hermes", hermes_agent_slack_app_token="from-domain")
    assert when(hermes_agent_id="donna", hermes_agent_slack_app_token="")
    assert not when(hermes_agent_id="donna", hermes_agent_slack_app_token="from-domain")
    assert not when(hermes_agent_id="pepper", hermes_agent_slack_app_token="")


if __name__ == "__main__":
    test_bot_token_fallback_reads_the_agent_specific_field_for_both_agents()
    test_app_token_platform_field_is_a_fallback_for_donna_only()
    print("all checks passed")
