"""Both Slack agents fall back to their own platform-env bot token field. Runs bare or under pytest."""
from pathlib import Path

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


if __name__ == "__main__":
    test_bot_token_fallback_reads_the_agent_specific_field_for_both_agents()
    print("all checks passed")
