"""Hermes refuses to start the dashboard without an auth provider.

`hermes dashboard` exits 1 when its auth gate engages (any non-loopback bind, or
a non-loopback dashboard.public_url) and no DashboardAuthProvider registered, so
the unit crash-loops. The bundled self-hosted OIDC provider registers only when
an https issuer and a client_id reach config.yaml. These tests pin the three
guards: the render-time assert, the config wiring it protects, and the
post-converge gate that tells a dead unit from a missing login redirect.
"""

from __future__ import annotations

import ast

import pytest
import yaml
from jinja2 import Environment

from _role_files import role_defaults, role_tasks
from conftest import ROLE_ROOT
from test_context_limits import _render, _sample_limits

GOOD = {
    "hermes_agent_dashboard_oidc_issuer": "https://auth.example.invalid",
    "hermes_agent_dashboard_oidc_client_id": "hermes-dashboard",
    "hermes_agent_dashboard_public_url": "https://hermes.example.invalid",
}


def _task(tasks: list, name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _problems(**overrides: str) -> list[str]:
    """Evaluate the role's own problem-collecting expression."""
    task = _task(
        role_tasks(ROLE_ROOT),
        "Collect the dashboard auth settings that are missing or malformed",
    )
    expression = task["ansible.builtin.set_fact"]["hermes_agent_dashboard_auth_problems"]
    rendered = Environment().from_string(expression).render(**{**GOOD, **overrides})
    return ast.literal_eval(rendered)


def test_complete_settings_pass() -> None:
    assert _problems() == []


@pytest.mark.parametrize(
    ("override", "missing"),
    [
        # A blank PROXMOX_SUBDOMAIN renders a trailing-dot host that is not empty.
        ({"hermes_agent_dashboard_oidc_issuer": "https://authelia."}, "hermes_agent_dashboard_oidc_issuer"),
        ({"hermes_agent_dashboard_oidc_issuer": ""}, "hermes_agent_dashboard_oidc_issuer"),
        ({"hermes_agent_dashboard_oidc_issuer": "http://auth.example.invalid"}, "hermes_agent_dashboard_oidc_issuer"),
        ({"hermes_agent_dashboard_oidc_client_id": ""}, "hermes_agent_dashboard_oidc_client_id"),
        ({"hermes_agent_dashboard_public_url": "https://hermes."}, "hermes_agent_dashboard_public_url"),
        ({"hermes_agent_dashboard_public_url": ""}, "hermes_agent_dashboard_public_url"),
    ],
)
def test_missing_or_malformed_setting_is_named(override: dict, missing: str) -> None:
    assert _problems(**override) == [missing]


def test_assert_fails_on_any_problem_and_runs_before_anything_deploys() -> None:
    tasks = role_tasks(ROLE_ROOT)
    check = _task(tasks, "Assert the dashboard has an auth provider configured before it is started")
    assert check["ansible.builtin.assert"]["that"] == [
        "hermes_agent_dashboard_auth_problems | length == 0"
    ]
    assert "hermes_agent_dashboard_auth_problems | join(', ')" in check["ansible.builtin.assert"]["fail_msg"]
    # The assert is reached through main.yml's first include; every deploy task
    # sits after it. role_tasks flattens includes, so order the raw main.yml.
    main = yaml.safe_load((ROLE_ROOT / "tasks" / "main.yml").read_text())
    names = [task.get("name") for task in main]
    for later in (
        "Install the pinned Hermes runtime",
        "Deploy secrets, config.yaml, persona content, and Hindsight provider config",
        "Deploy and start the Hermes gateway and dashboard units",
    ):
        assert names.index("Assert brain-wiring contracts before converging") < names.index(later), later
    flat = [task.get("name") for task in tasks]
    assert flat.index(check["name"]) < flat.index("Deploy the Hermes config.yaml (brain -> LiteLLM router, memory -> Hindsight, budget)")


def test_config_wires_the_guarded_settings_into_the_oidc_provider() -> None:
    _, config = _render("config.yaml.j2", _sample_limits())
    dashboard = config["dashboard"]
    assert dashboard["oauth"]["provider"] == "self-hosted"
    # Same variables the assert guards; the sample context pins their values.
    assert dashboard["oauth"]["self_hosted"]["issuer"] == "https://auth.example.invalid"
    assert dashboard["oauth"]["self_hosted"]["client_id"] == "fixture-client"
    assert dashboard["public_url"] == "https://dashboard.example.invalid"
    defaults = role_defaults(ROLE_ROOT)
    for key in GOOD:
        assert key in defaults


def test_dashboard_unit_state_is_checked_before_the_auth_redirect() -> None:
    gates = role_tasks(ROLE_ROOT, "verify_dashboard.yml")
    names = [gate["name"] for gate in gates]
    ordered = [
        "Gate — the dashboard unit is active",
        "Read the dashboard journal when the unit is not active",
        "Gate — fail with the dashboard journal when the unit never came up",
        "Gate — the dashboard requires authentication (302 to login)",
    ]
    assert [names.index(name) for name in ordered] == sorted(names.index(name) for name in ordered)
    unit = _task(gates, ordered[0])
    assert unit["ansible.builtin.systemd"]["name"] == "hermes-dashboard"
    journal = _task(gates, ordered[1])
    assert "--unit=hermes-dashboard" in journal["ansible.builtin.command"]["argv"]
    assert journal["when"] == "hermes_agent_dashboard_unit.status.ActiveState | default('') != 'active'"
    message = _task(gates, ordered[2])["ansible.builtin.assert"]["fail_msg"]
    assert "hermes_agent_dashboard_journal.stdout" in message
    assert "not an authentication-redirect problem" in message


def test_verify_includes_the_dashboard_gates_inside_its_shared_rescue_block() -> None:
    text = (ROLE_ROOT / "tasks" / "verify.yml").read_text()
    verify = yaml.safe_load(text)
    block = next(task for task in verify if task.get("name") == "Post-converge verification gate")
    assert any(
        task.get("ansible.builtin.include_tasks") == "verify_dashboard.yml" for task in block["block"]
    )
    assert "rescue" in block
    # The gates exist once, in the included file.
    assert "requires authentication (302" not in text
