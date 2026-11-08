"""The preflight guard enforces one Slack socket per identity.

tasks/assert_slack_single_socket.yml renders the guest's env templates the
way the play deploys them and fails the converge when a named profile would
receive a Slack token, or when two gateways on one guest would receive the
same non-empty SLACK_APP_TOKEN (the runtime parks such a duplicate at
startup, disabling Slack on the second gateway). These tests re-evaluate the
task's own extraction and condition expressions against fixtures and the
deployed templates, so a condition that stops matching the env shape fails
here rather than passing vacuously.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from jinja2 import Environment

from _role_files import role_defaults, role_tasks
from test_profile_contract import ROLE_ROOT, _jinja_env, _profiles
from test_profile_env_parity import _context as _env_render_context

ASSERT_FILE = ROLE_ROOT / "tasks" / "assert_slack_single_socket.yml"
TASKS = yaml.safe_load(ASSERT_FILE.read_text())
DEFAULTS = role_defaults(ROLE_ROOT)
ASSERT_IMPORT_NAME = "Assert one Slack socket per identity (no duplicated Slack token)"

MAIN_ENV_TASK = next(
    task for task in TASKS
    if task.get("name") == "Assert the guest's two gateways never share a Slack app token"
    and task["vars"].get("hermes_agent_slack_socket_main_env")
)
PUBLIC_ENV_TASK = MAIN_ENV_TASK
PROFILE_COLLECT_TASK = next(
    task for task in TASKS
    if task["ansible.builtin.set_fact"].get("hermes_agent_slack_socket_profile_tokens")
)
GATEWAY_ASSERT_TASK = next(
    task for task in TASKS
    if "ansible.builtin.assert" in task and "two gateways" in task["name"]
)
PROFILE_ASSERT_TASK = next(
    task for task in TASKS
    if "ansible.builtin.assert" in task and "named profile" in task["name"]
)


def _evaluation_env() -> Environment:
    env = Environment(autoescape=False)
    env.tests["match"] = lambda value, pattern: re.match(pattern, str(value)) is not None
    return env


def _rendered_env(name: str, **context: Any) -> str:
    """Render an env template the way the converge would.

    The parity test's context is the proven full render context for both env
    templates (defaults + every conditional gate); the Slack token values and
    the public-gateway identity are pinned here so the token lines render
    real values, and a caller override wins last.
    """
    template = _jinja_env().from_string((ROLE_ROOT / "templates" / name).read_text())
    merged = {
        **_env_render_context(),
        "hermes_agent_slack_bot_token": "xoxb-main",
        "hermes_agent_slack_app_token": "xapp-main",
        **context,
    }
    return template.render(**merged)


def _native(env: Environment, expression: str, context: dict) -> Any:
    """Evaluate one Ansible templated expression natively (lists stay lists)."""
    text = str(expression).strip()
    if text.startswith("{{") and text.endswith("}}"):
        text = text[2:-2].strip()
    return env.compile_expression(text)(**context)


def _collect_profile_tokens(rendered_env: str, profile: dict) -> list[dict]:
    """Run the collect task's own accumulation expression for one profile."""
    env = _evaluation_env()
    return _native(
        env,
        PROFILE_COLLECT_TASK["ansible.builtin.set_fact"][
            "hermes_agent_slack_socket_profile_tokens"
        ],
        {
            "hermes_agent_slack_socket_profile_env": rendered_env,
            "hermes_agent_slack_socket_profile_tokens": [],
            "item": profile,
        },
    )


def _evaluate_assert(task: dict, context: dict) -> tuple[bool, dict]:
    """Evaluate one assert task's vars and `that` clauses as Ansible would.

    A var whose expression is a controller lookup (or which the fixture
    overrides) is taken from ``context`` instead, the way a fixture stands in
    for the rendered env text in the condition checks.
    """
    env = _evaluation_env()
    resolved = {}
    for key, expression in (task.get("vars") or {}).items():
        if key in context or "lookup(" in str(expression):
            continue
        resolved[key] = _native(env, expression, {**context, **resolved})
    verdicts = [
        bool(env.compile_expression(str(clause))(**context, **resolved))
        for clause in task["ansible.builtin.assert"]["that"]
    ]
    return all(verdicts), resolved


def test_assertion_is_wired_into_the_layer_1_preflight() -> None:
    flat_names = [task.get("name", "") for task in role_tasks(ROLE_ROOT)]
    assert ASSERT_IMPORT_NAME in flat_names
    assert flat_names.index(ASSERT_IMPORT_NAME) < flat_names.index(
        "Install Hermes system prerequisites and normalize the checkout refspec"
    )
    tag_map = DEFAULTS["hermes_agent_entry_point_task_tags"]
    assert tag_map["assert_slack_single_socket.yml"]


def test_tasks_touching_rendered_env_content_are_no_log() -> None:
    assert PROFILE_COLLECT_TASK["no_log"] is True
    assert GATEWAY_ASSERT_TASK["no_log"] is True
    # The gateway pair check renders its env templates into TASK VARS (never
    # facts), so the rendered text is transient; it stays no_log regardless.
    assert "ansible.builtin.set_fact" not in GATEWAY_ASSERT_TASK
    assert MAIN_ENV_TASK is GATEWAY_ASSERT_TASK and PUBLIC_ENV_TASK is MAIN_ENV_TASK


def test_collect_expression_extracts_blank_tokens_from_the_deployed_profile_env() -> None:
    for profile in _profiles():
        rendered = _rendered_env("hermes-env-profile.j2", hermes_agent_profile=profile)
        entry = _collect_profile_tokens(rendered, profile)[0]
        assert entry["name"] == profile["name"]
        assert entry["bot"] == "" and entry["app"] == "", profile["name"]


def test_gateway_pair_condition_fails_on_a_shared_app_token() -> None:
    shared = "xapp-shared"
    verdict, _ = _evaluate_assert(
        GATEWAY_ASSERT_TASK,
        {
            "hermes_agent_slack_socket_main_env": f"SLACK_APP_TOKEN={shared}\n",
            "hermes_agent_slack_socket_public_env": f"SLACK_APP_TOKEN={shared}\n",
        },
    )
    assert verdict is False, "two gateways with the same non-empty app token must fail"


def test_gateway_pair_condition_passes_without_a_public_token() -> None:
    verdict, _ = _evaluate_assert(
        GATEWAY_ASSERT_TASK,
        {
            "hermes_agent_slack_socket_main_env": "SLACK_APP_TOKEN=xapp-main\n",
            "hermes_agent_slack_socket_public_env": "SLACK_BOT_TOKEN=\n",
        },
    )
    assert verdict is True


def test_gateway_pair_condition_passes_on_distinct_tokens() -> None:
    verdict, _ = _evaluate_assert(
        GATEWAY_ASSERT_TASK,
        {
            "hermes_agent_slack_socket_main_env": "SLACK_APP_TOKEN=xapp-main\n",
            "hermes_agent_slack_socket_public_env": "SLACK_APP_TOKEN=xapp-public\n",
        },
    )
    assert verdict is True


def test_deployed_gateway_templates_hold_one_socket_and_pass_the_pair_condition() -> None:
    main_env = _rendered_env("hermes-env.j2")
    public_env = _rendered_env("hermes-env-public-gateway.j2")
    verdict, _ = _evaluate_assert(
        GATEWAY_ASSERT_TASK,
        {
            "hermes_agent_slack_socket_main_env": main_env,
            "hermes_agent_slack_socket_public_env": public_env,
        },
    )
    assert verdict is True
    # The main gateway holds the identity's own socket; the public gateway env
    # never renders a Slack token at all.
    def value(env_text: str, key: str) -> str:
        return next(
            (line for line in env_text.split("\n") if line.startswith(f"{key}=")),
            f"{key}=",
        ).split("=", 1)[-1]

    assert value(main_env, "SLACK_APP_TOKEN") == "xapp-main"
    assert value(main_env, "SLACK_BOT_TOKEN") == "xoxb-main"
    assert value(public_env, "SLACK_APP_TOKEN") == ""


def test_profile_offender_condition_lists_only_token_holders() -> None:
    verdict, resolved = _evaluate_assert(
        PROFILE_ASSERT_TASK,
        {
            "hermes_agent_slack_socket_profile_tokens": [
                {"name": "clean", "bot": "", "app": ""},
                {"name": "app-holder", "bot": "", "app": "xapp-leak"},
                {"name": "bot-holder", "bot": "xoxb-leak", "app": ""},
            ],
        },
    )
    assert verdict is False
    offenders = resolved["hermes_agent_slack_socket_profile_offenders"]
    assert sorted(offenders) == ["app-holder", "bot-holder"]

    verdict, resolved = _evaluate_assert(
        PROFILE_ASSERT_TASK,
        {
            "hermes_agent_slack_socket_profile_tokens": [
                {"name": "clean", "bot": "", "app": ""},
            ],
        },
    )
    assert verdict is True
    assert resolved["hermes_agent_slack_socket_profile_offenders"] == []


def test_deployed_profile_envs_pass_the_profile_condition() -> None:
    tokens: list[dict] = []
    for profile in _profiles():
        rendered = _rendered_env("hermes-env-profile.j2", hermes_agent_profile=profile)
        tokens.extend(_collect_profile_tokens(rendered, profile))
    verdict, resolved = _evaluate_assert(
        PROFILE_ASSERT_TASK, {"hermes_agent_slack_socket_profile_tokens": tokens}
    )
    assert verdict is True, resolved["hermes_agent_slack_socket_profile_offenders"]
