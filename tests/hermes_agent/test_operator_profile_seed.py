"""USER.md is seeded once and then belongs to the agent.

Upstream loads USER.md alongside MEMORY.md whenever ``user_profile_enabled`` is
set, and Hermes WRITES to it: it is where the agent records what it learns about
the operator. That makes it categorically different from SOUL.md, which this
role owns and re-copies on every converge.

The failure this file exists to prevent is quiet and total. A converge that
deployed USER.md the way it deploys SOUL.md — a plain template with no
``force: false`` — would overwrite the agent's accumulated profile on every
single run, and nothing would report it: the file would still be there, still
well-formed, just reset to the seed. The loss is invisible until someone
notices Hermes has stopped knowing things it used to know.

So the assertions here are about OWNERSHIP, not content:

  - the seed exists and is gated by its own switch;
  - the deploy is non-destructive (``force: false``);
  - the seed is public-safe, because this repo is public and the template ships
    in it — working preferences and channel semantics only, never an address,
    a hostname, or a credential.

Runs bare (``python3 tests/hermes_agent/test_operator_profile_seed.py``) or
under pytest.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from _role_files import role_defaults, role_tasks_text

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
MAIN_TASKS = role_tasks_text(ROLE)
TEMPLATE = ROLE / "templates" / "USER.md.j2"
CONFIG_TEMPLATE = (ROLE / "templates" / "config.yaml.j2").read_text()


def _seed_task() -> str:
    """The USER.md deploy task block, from its name to the next task."""
    match = re.search(
        r"^- name: Seed the Hermes operator profile.*?(?=^- name: |\Z)",
        MAIN_TASKS,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "the USER.md seed task is missing from tasks/main.yml"
    return match.group(0)


def test_the_seed_template_exists() -> None:
    assert TEMPLATE.is_file(), f"missing {TEMPLATE}"


def test_the_profile_is_enabled_upstream() -> None:
    """Seeding a file upstream never reads would be dead weight."""
    assert "user_profile_enabled: true" in CONFIG_TEMPLATE


def test_the_seed_never_overwrites_the_agents_own_profile() -> None:
    """THE contract. Hermes writes to USER.md; a converge must not.

    force:false makes the template create-if-absent. Without it every converge
    silently resets the agent's accumulated knowledge of the operator back to
    the seed, leaving a valid-looking file and no error anywhere.
    """
    task = _seed_task()
    assert re.search(r"^\s+force:\s*false\s*$", task, re.MULTILINE), (
        "the USER.md seed task must set force: false — without it the converge "
        "overwrites the agent-owned profile on every run"
    )


def test_the_seed_is_gated_by_its_own_switch() -> None:
    """Separate from hermes_agent_soul_managed: SOUL.md is role-owned and
    USER.md is agent-owned, so one flag cannot mean both."""
    assert DEFAULTS["hermes_agent_seed_user_profile"] is True
    assert "hermes_agent_seed_user_profile" in _seed_task()


def test_the_seed_carries_the_operators_stated_schedule() -> None:
    """The operator asked for a profile so their working day survives a lost
    session. The four times are the load-bearing part."""
    body = TEMPLATE.read_text()
    for hour in ("08:00", "12:00", "15:00", "20:00"):
        assert hour in body, f"the seed does not record the {hour} slot"


def test_the_seed_records_the_write_routing_law() -> None:
    """The one rule with a public-exposure consequence if forgotten."""
    body = TEMPLATE.read_text().lower()
    assert "zammad" in body and "vikunja" in body
    assert "never" in body and "github issue" in body


def test_the_seed_leaks_nothing_into_a_public_repo() -> None:
    """This template ships in a public repository. It may describe HOW the
    operator works; it may not describe WHERE anything lives."""
    body = TEMPLATE.read_text()

    ipv4 = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", body)
    assert not ipv4, f"the seed contains an address: {ipv4}"

    # Bare domains and hostnames. The Jinja comment/ansible_managed header and
    # ordinary prose punctuation are excluded by requiring a TLD-shaped tail.
    hosts = re.findall(r"\b[a-z0-9][a-z0-9-]*\.(?:com|net|org|io|ai|local|internal)\b", body)
    assert not hosts, f"the seed names a host: {hosts}"

    lowered = body.lower()
    for secret_word in ("password", "token", "api_key", "api key", "secret_id"):
        assert secret_word not in lowered, f"the seed mentions {secret_word!r}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
