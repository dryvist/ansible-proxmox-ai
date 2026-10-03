"""Contract for the `github` webhook route in config.yaml.j2 and its script filter."""
import json
import re
import runpy
import types
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from _pr_reconcile_shared import load_module
from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TEMPLATE = (ROLE / "templates" / "config.yaml.j2").read_text()
BLOCK = TEMPLATE[TEMPLATE.index("{% set _slack_on"):TEMPLATE.index("{% set _mcp_splunk")]
FILTER = types.SimpleNamespace(
    **runpy.run_path(str(ROLE / "files" / "github-route-same-repo.py"), run_name="filt")
)


def render(private: bool, public: bool = False, public_profile: str = "") -> dict:
    env = Environment(
        autoescape=select_autoescape(default=False, default_for_string=False),
        loader=FileSystemLoader(ROLE / "templates"),
    )
    env.filters["to_json"] = json.dumps
    env.filters["bool"] = bool
    d = role_defaults(ROLE)
    ctx = {k: v for k, v in d.items() if k.startswith("hermes_agent_github_route_")}
    ctx.update(
        hermes_agent_github_route_private_enabled=private,
        hermes_agent_github_route_public_enabled=public,
        hermes_agent_github_route_public_profile=public_profile,
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


def test_routes_split_by_visibility():
    assert list(render(True)) == ["github-private"]
    both = render(True, True, "pr-public")
    assert list(both) == ["github-private", "github-public"]
    assert both["github-public"]["profile"] == "pr-public"
    assert "profile" not in both["github-private"]
    vis = {n: {f["field"]: f for f in r["filters"]}["repository.private"]["equals"] for n, r in both.items()}
    assert vis == {"github-private": True, "github-public": False}


def test_github_route_shape():
    route = render(True)["github-private"]
    assert route["events"] == ["pull_request"]
    assert route["deliver"] == "log"
    assert "deliver_only" not in route and "cron_job" not in route
    assert route["prompt"] == (
        "Review pull request #{pull_request.number} in {repository.full_name} at head commit {pull_request.head.sha}."
    )
    assert route["skills"] == ["dryvist-pr-review"]
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


def test_prompt_fields_resolve_in_the_reconcile_payload():
    """Every {dotted.path} in the route prompt exists in the reconciler's
    synthetic event, so replayed and live deliveries render the same prompt."""
    pr = {
        "number": 7,
        "head": {"sha": "abc", "repo": {"full_name": "o/r"}},
        "base": {"ref": "develop", "repo": {"full_name": "o/r", "private": True}},
    }
    payload = load_module().build_payload("o/r", pr)
    for path in re.findall(r"\{([a-z_.]+)\}", render(True)["github-private"]["prompt"]):
        value = payload
        for part in path.split("."):
            value = value[part]
        assert value not in (None, "")
