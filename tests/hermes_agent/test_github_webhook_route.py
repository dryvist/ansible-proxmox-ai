"""Contract for the `github` webhook route in config.yaml.j2 and its script filter."""
import json
import runpy
import types
from pathlib import Path

import yaml
from jinja2 import Environment

from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TEMPLATE = (ROLE / "templates" / "config.yaml.j2").read_text()
BLOCK = TEMPLATE[TEMPLATE.index("{% set _slack_on"):TEMPLATE.index("{% set _mcp_splunk")]
FILTER = types.SimpleNamespace(
    **runpy.run_path(str(ROLE / "files" / "github-route-same-repo.py"), run_name="filt")
)


def render(enabled: bool) -> dict:
    env = Environment(autoescape=False)  # noqa: S701
    env.filters["to_json"] = json.dumps
    env.filters["bool"] = bool
    d = role_defaults(ROLE)
    ctx = {k: v for k, v in d.items() if k.startswith("hermes_agent_github_route_")}
    ctx.update(
        hermes_agent_github_route_enabled=enabled,
        hermes_agent_github_route_bot_login="example-bot[bot]",
        hermes_agent_slack_bot_token="xoxb",
        hermes_agent_slack_reply_in_thread=False,
        hermes_agent_slack_cron_continuable_surface="in_channel",
        hermes_agent_webhook_enabled=True,
        hermes_agent_webhook_secret="s",
        hermes_agent_webhook_port=8644,
        hermes_agent_webhook_prompt="p",
    )
    return yaml.safe_load(env.from_string(BLOCK).render(**ctx))["platforms"]["webhook"]["extra"]["routes"]


def test_disabled_keeps_inbound_demo():
    assert list(render(False)) == ["inbound"]


def test_github_route_shape():
    route = render(True)["github"]
    assert list(render(True)) == ["github"]
    assert route["events"] == ["pull_request"]
    assert route["deliver"] == "log"
    assert "deliver_only" not in route and "cron_job" not in route and "prompt" not in route
    assert route["coalesce"] == {
        "key": "{repository.full_name}#{pull_request.number}",
        "window_seconds": 60,
        "max_wait_seconds": 300,
    }
    assert route["skills"] == ["pr-review"]
    assert route["toolsets"] == ["terminal", "web"]
    filters = {f["field"]: f for f in route["filters"]}
    assert filters["repository.owner.login"]["equals"] == "dryvist"
    assert filters["action"]["in"] == ["opened", "synchronize", "reopened", "ready_for_review"]
    assert filters["pull_request.draft"]["not_equals"] is True
    assert filters["sender.login"]["not_equals"] == "example-bot[bot]"
    assert route["script"] == "github-route-same-repo.py"


def test_same_repo_filter():
    pr = {"repository": {"full_name": "o/r"}, "pull_request": {"head": {"repo": {"full_name": "o/r"}}}}
    assert FILTER.same_repo(pr)
    fork = {"repository": {"full_name": "o/r"}, "pull_request": {"head": {"repo": {"full_name": "x/r"}}}}
    assert not FILTER.same_repo(fork)
    deleted = {"repository": {"full_name": "o/r"}, "pull_request": {"head": {"repo": None}}}
    assert not FILTER.same_repo(deleted)
    assert not FILTER.same_repo({})
