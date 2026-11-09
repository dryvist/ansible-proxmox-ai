"""One Slack socket per identity: the preflight assertion contract.

SLACK_APP_TOKEN is the Socket Mode credential. Two gateways on one guest
rendering the same non-empty value open two sockets on one Slack app, and the
runtime refuses the duplicate claim and disables Slack on the second — the
state that parked named profiles' cron delivery. Upstream hermes-agent cannot
deliver a profile's cron output on the bot token alone (SlackAdapter.connect
fatally requires SLACK_APP_TOKEN before Socket Mode starts, and a profile
holding a bot credential is served by its own adapters, never the primary's),
so named profiles hold neither token and post through the main gateway's
profile_routes (defaults/main/152-profile-slack-routes.yml).

tasks/assert_slack_single_socket.yml encodes that rule at converge time. This
module pins the file's wiring and evaluates its `that` conditions against the
real role files and against doctored fixtures, the way
test_slack_bot_token_fallback.py compiles task-file expressions.
"""

from __future__ import annotations

import re

import jinja2
import yaml

from _role_files import role_defaults
from test_profile_contract import ROLE_ROOT

TASK_FILE = ROLE_ROOT / "tasks" / "assert_slack_single_socket.yml"
ASSERT_FILE = ROLE_ROOT / "tasks" / "assert.yml"
PROFILE_ENV = (ROLE_ROOT / "templates" / "hermes-env-profile.j2").read_text()
PUBLIC_ENV = (ROLE_ROOT / "templates" / "hermes-env-public-gateway.j2").read_text()

# The exact render lines the one-socket rule replaced: any of these in the
# named-profile env template would put the identity's shared token in a second
# env file on the guest.
SHARED_TOKEN_RENDER_LINES = (
    "SLACK_BOT_TOKEN={{ hermes_agent_slack_bot_token if 'slack' in hermes_agent_profile.env else '' }}",
    "SLACK_APP_TOKEN={{ hermes_agent_slack_app_token if 'slack' in hermes_agent_profile.env else '' }}",
)


def _tasks() -> list[dict]:
    return yaml.safe_load(TASK_FILE.read_text())


def _conditions() -> list[str]:
    task = next(t for t in _tasks() if "ansible.builtin.assert" in t)
    return task["ansible.builtin.assert"]["that"]


def _evaluate(sources: list[str], profiles: list[dict]) -> list[bool]:
    env = jinja2.Environment()
    env.tests["contains"] = lambda seq, item: item in seq
    env.tests["search"] = lambda value, pattern: re.search(pattern, str(value)) is not None
    context = {
        "hermes_agent_slack_socket_env_sources": sources,
        "hermes_agent_profiles": profiles,
    }
    return [
        bool(env.compile_expression(condition)(**context))
        for condition in _conditions()
    ]


def test_preflight_is_wired_into_the_role_with_no_log() -> None:
    imports = [
        task for task in yaml.safe_load(ASSERT_FILE.read_text())
        if task.get("ansible.builtin.import_tasks") == "assert_slack_single_socket.yml"
    ]
    assert len(imports) == 1
    assert (
        imports[0]["tags"]
        == "{{ hermes_agent_entry_point_task_tags['assert_slack_single_socket.yml'] }}"
    )
    tag_map = yaml.safe_load(
        (ROLE_ROOT / "defaults" / "main" / "196-entry-point-tags.yml").read_text()
    )["hermes_agent_entry_point_task_tags"]
    assert tag_map["assert_slack_single_socket.yml"] == "hermes_runtime_install"
    for task in _tasks():
        assert task.get("no_log") is True, task["name"]


def test_preflight_passes_on_current_role_files_and_defaults() -> None:
    profiles = role_defaults(ROLE_ROOT)["hermes_agent_profiles"]
    results = _evaluate([PROFILE_ENV, PUBLIC_ENV], profiles)
    assert all(results), [c for c, ok in zip(_conditions(), results) if not ok]


def test_preflight_fails_on_a_profile_template_rendering_the_shared_token() -> None:
    doctored = PROFILE_ENV
    for blank, shared in zip(
        ("\nSLACK_BOT_TOKEN=\n", "\nSLACK_APP_TOKEN=\n"), SHARED_TOKEN_RENDER_LINES
    ):
        assert blank in doctored
        doctored = doctored.replace(blank, "\n" + shared + "\n")
    profiles = role_defaults(ROLE_ROOT)["hermes_agent_profiles"]
    results = _evaluate([doctored, PUBLIC_ENV], profiles)
    assert not all(results)


def test_preflight_fails_when_a_second_gateway_carries_the_app_token() -> None:
    # The standalone public gateway is the only other gateway unit on the
    # guest; its env naming the app token is exactly the two-sockets-one-app
    # shape the runtime disables Slack for.
    results = _evaluate([PROFILE_ENV, PUBLIC_ENV + "\nSLACK_APP_TOKEN=xapp-shared\n"], [])
    assert not all(results)


def test_preflight_fails_when_a_named_profile_grants_the_slack_section() -> None:
    # 'slack' in a profile env is the grant that once carried the identity's
    # token pair into the profile home; the fixture asserts the converge fails
    # before such a profile can render anything.
    profiles = role_defaults(ROLE_ROOT)["hermes_agent_profiles"] + [
        {"name": "slack-clones", "env": ["slack"], "mcp": [], "skills": []}
    ]
    results = _evaluate([PROFILE_ENV, PUBLIC_ENV], profiles)
    assert not all(results)
