"""Restart-safe cron worker dispatch needs a reachable user D-Bus session.

Upstream (hermes-agent v2026.9.11+) launches each due cron job in a transient
`systemd-run --user --scope` so the worker survives a gateway restart. That
needs `/run/user/<uid>/bus`, which a plain `ansible.builtin.user: system:
true` account never gets — nothing starts `user@<uid>.service` for it.
Measured live 2026-09-23: 80 dispatch failures in ~10h with exactly the
symptom upstream's own bug tracker documents for this gap
(NousResearch/hermes-agent#110628), whose fix is `loginctl enable-linger
<user>` plus ordering the long-running service after `user@<uid>.service` so
a cold boot doesn't race the two.
"""

from __future__ import annotations

from pathlib import Path

from _role_files import role_tasks_text

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TASKS = role_tasks_text(ROLE)
GATEWAY_UNIT = (ROLE / "templates" / "hermes-gateway.service.j2").read_text()


def test_linger_is_enabled_for_the_hermes_user() -> None:
    assert "loginctl enable-linger" in TASKS, (
        "restart-safe cron dispatch needs a user D-Bus session; "
        "nothing enables systemd lingering for the hermes user"
    )


def test_linger_task_is_idempotent() -> None:
    """A bare `loginctl enable-linger` command with no `creates:`/`changed_when:`
    reports changed on every converge — cheap to get right, easy to regress.
    """
    task_block = TASKS.split("Enable systemd user lingering", 1)[1].split("- name:", 1)[0]
    assert "creates:" in task_block, (
        "loginctl enable-linger task has no creates: guard — reports changed every run"
    )


def test_linger_state_marker_matches_reality() -> None:
    """`loginctl enable-linger <user>` writes /var/lib/systemd/linger/<user> —
    pin the exact path so a typo'd `creates:` silently defeats the idempotency
    guard (the task would then run, and report changed, every converge).
    """
    task_block = TASKS.split("Enable systemd user lingering", 1)[1].split("- name:", 1)[0]
    assert "/var/lib/systemd/linger/{{ hermes_agent_user }}" in task_block


def test_gateway_unit_orders_after_the_user_session() -> None:
    """Linger alone only takes effect from the NEXT boot / login manager
    start; without ordering, a cold boot can still start the gateway before
    user@<uid>.service is up.
    """
    assert "user@{{ hermes_agent_uid }}.service" in GATEWAY_UNIT
    assert "After=user@{{ hermes_agent_uid }}.service" in GATEWAY_UNIT
    assert "Wants=user@{{ hermes_agent_uid }}.service" in GATEWAY_UNIT


def test_hermes_agent_uid_is_derived_not_hardcoded() -> None:
    """The uid must come from a live getent lookup, not a guessed literal —
    a hardcoded 999/1000 silently mis-orders the unit on any guest where the
    hermes user happens to land on a different uid.
    """
    assert "ansible.builtin.getent" in TASKS
    assert "database: passwd" in TASKS
    assert "hermes_agent_uid" in TASKS
    assert "set_fact" in TASKS
