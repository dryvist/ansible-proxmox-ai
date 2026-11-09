"""Fail when a component tag reaches a bao-approle caller the CLI install
does not carry.

One main.yml entry installs the shared OpenBao AppRole CLI on the guest,
narrowed to static component tags like every other include. The guest
surfaces that invoke `bao-approle` — the gh-token service the profiles
component starts at converge, the pepper vault refresh script, the
brain-sync fetch script under the units component — fail inside their own
scoped run when that install is not selected with them. Every caller must
be deployed (or invoked) under component tags the install entry carries.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from _role_files import _task_items, include_file, template_text

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "roles" / "hermes_agent"
TASKS = ROLE / "tasks"
COMPONENT_TAGS = {
    "hermes_runtime_install",
    "hermes_patches",
    "hermes_profiles_env",
    "hermes_content_scripts",
    "hermes_crons",
    "hermes_units_gateways",
    "hermes_delivery_probe",
}
INSTALL_ROLE = "dryvist.homelab.bao_approle_cli"
_BAO_CALL = re.compile(r"\bbao-approle\b")
# Callers that exist today; if one disappears or a new one appears, this
# test must be revisited, not silently narrowed.
KNOWN_CALLERS = (
    "hermes-gh-token.py",
    "hermes-brain-sync.sh.j2",
    "hermes-pepper-vault-refresh.sh.j2",
)


def _component_tags(task: dict) -> set[str]:
    tags = task.get("tags", [])
    tags = [tags] if isinstance(tags, str) else list(tags or [])
    return set(tags) & COMPONENT_TAGS


def _caller_artifacts() -> dict[str, str]:
    """Role artifacts whose rendered content invokes bao-approle, as
    (file name -> text). Template Jinja includes are spliced in, so a call
    inside a shared partial still counts for its including template."""
    callers: dict[str, str] = {}
    for path in sorted(ROLE.glob("templates/*")) + sorted(ROLE.glob("files/*")):
        if not path.is_file():
            continue
        text = template_text(ROLE, path.name) if path.suffix == ".j2" else path.read_text()
        if _BAO_CALL.search(text):
            callers[path.name] = text
    return callers


def _deployer_files(caller_names: set[str]) -> set[str]:
    """Tasks files that reference a caller artifact or invoke bao-approle
    directly in a task of their own."""
    deployers: set[str] = set()
    for path in sorted(TASKS.glob("*.yml")):
        if path.name == "main.yml":
            continue
        text = path.read_text()
        if _BAO_CALL.search(text) or any(name in text for name in caller_names):
            deployers.add(path.name)
    return deployers


def _owner_component_tags(filename: str, main: list[dict]) -> set[str]:
    """The component tags of the main.yml entry that statically imports the
    file — a deployer is covered exactly when its importing entry is."""
    owners = [
        task
        for task in _task_items(main)
        if include_file(task, "ansible.builtin.import_tasks") == filename
    ]
    assert owners, (
        f"{filename} is no longer statically imported by tasks/main.yml — "
        "resolve its component tags here"
    )
    tags: set[str] = set()
    for task in owners:
        tags |= _component_tags(task)
    return tags


def test_every_bao_approle_caller_is_covered_by_the_install_tags() -> None:
    main = yaml.safe_load((TASKS / "main.yml").read_text()) or []
    callers = _caller_artifacts()
    for known in KNOWN_CALLERS:
        assert known in callers, known

    install = [
        task
        for task in _task_items(main)
        if isinstance(task.get("ansible.builtin.import_role"), dict)
        and task["ansible.builtin.import_role"].get("name") == INSTALL_ROLE
    ]
    assert len(install) == 1, "the bao-approle CLI install moved — update this test"

    required: set[str] = set()
    for filename in _deployer_files(set(callers)):
        required |= _owner_component_tags(filename, main)
    assert required, "no deployer resolved a component tag — the scan went blind"

    missing = required - _component_tags(install[0])
    assert not missing, (
        "components deploying bao-approle callers that the CLI install task "
        f"does not carry: {sorted(missing)}"
    )
