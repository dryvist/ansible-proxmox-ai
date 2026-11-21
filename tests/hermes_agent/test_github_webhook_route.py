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


def render(private: bool, public: bool = False, **extra) -> dict:
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
        hermes_agent_github_route_bot_login="example-bot[bot]",
        hermes_agent_slack_bot_token="xoxb",
        hermes_agent_slack_reply_in_thread=False,
        hermes_agent_slack_cron_continuable_surface="in_channel",
        hermes_agent_webhook_enabled=True,
        hermes_agent_webhook_secret="s",
        hermes_agent_webhook_port=8644,
        hermes_agent_webhook_prompt="p",
    )
    ctx.update(extra)
    return yaml.safe_load(env.from_string(BLOCK).render(**ctx))["platforms"]["webhook"]["extra"]["routes"]


def test_disabled_keeps_inbound_demo():
    assert list(render(False)) == ["inbound"]


def test_default_gateway_renders_only_the_private_route():
    assert list(render(True)) == ["github-private"]
    both = render(True, True)
    assert list(both) == ["github-private"]
    assert "profile" not in both["github-private"]
    filters = {f["field"]: f for f in both["github-private"]["filters"]}
    assert filters["repository.private"]["equals"] is True


def test_public_route_alone_leaves_the_default_gateway_on_the_demo_route():
    assert list(render(False, True)) == ["inbound"]


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


def test_profile_routes_render_beside_the_private_route_without_a_secret():
    route = {"name": "task-event", "profile": "task-pm", "prompt": "Event {event_name} for {data.task.id}"}
    assert render(False, hermes_agent_profile_webhook_routes=[route]) == {
        "task-event": {"profile": "task-pm", "prompt": "Event {event_name} for {data.task.id}"},
    }
    both = render(True, hermes_agent_profile_webhook_routes=[route])
    assert list(both) == ["github-private", "task-event"]
    assert "secret" not in both["task-event"]


def test_profile_route_with_secret_env_renders_only_the_env_ref():
    route = {"name": "task-event", "profile": "task-pm", "prompt": "p",
             "secret_env": "TASK_EVENT_SECRET", "secret": "route-only-value"}
    rendered = render(False, hermes_agent_profile_webhook_routes=[route])["task-event"]
    assert rendered["secret"] == "${TASK_EVENT_SECRET}"
    assert "route-only-value" not in json.dumps(rendered)


def test_env_file_carries_each_route_secret_beside_the_global_one():
    env_tpl = (ROLE / "templates" / "hermes-env.j2").read_text()
    start = env_tpl.index("{% if hermes_agent_webhook_enabled")
    block = env_tpl[start:env_tpl.index("{% endif %}", start) + len("{% endif %}")]
    jinja = Environment(autoescape=select_autoescape(default=False, default_for_string=False))
    jinja.filters["bool"] = bool
    routes = [{"name": "a", "secret_env": "A_SECRET", "secret": "va"}, {"name": "b"}]
    out = jinja.from_string(block).render(
        hermes_agent_webhook_enabled=True, hermes_agent_webhook_secret="g",
        hermes_agent_webhook_port=8644, hermes_agent_profile_webhook_routes=routes)
    lines = [line for line in out.splitlines() if line]
    assert lines == ["WEBHOOK_ENABLED=true", "WEBHOOK_PORT=8644", "WEBHOOK_SECRET=g", "A_SECRET=va"]
