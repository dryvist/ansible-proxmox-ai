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

from _role_files import template_text, walk_tagged_tasks

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


def _component_tags(effective: tuple[str, ...]) -> set[str]:
    return set(effective) & COMPONENT_TAGS


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


def test_every_bao_approle_caller_is_covered_by_the_install_tags() -> None:
    callers = _caller_artifacts()
    for known in KNOWN_CALLERS:
        assert known in callers, known

    install: set[str] = set()
    required: set[str] = set()
    for task, effective, _source, opaque in walk_tagged_tasks(
        yaml.safe_load((TASKS / "main.yml").read_text()) or [],
        TASKS, (), "main.yml", self_tasks_dir=TASKS,
    ):
        text = yaml.dump(task, default_flow_style=False)
        if opaque:
            role = task.get("ansible.builtin.import_role")
            if isinstance(role, dict) and role.get("name") == INSTALL_ROLE:
                install = _component_tags(effective)
            continue
        if _BAO_CALL.search(text) or any(name in text for name in callers):
            required |= _component_tags(effective)
    assert install, "the bao-approle CLI install moved — update this test"
    assert required, "no deployer task resolved a component tag — the scan went blind"

    missing = required - install
    assert not missing, (
        "components deploying bao-approle callers that the CLI install task "
        f"does not carry: {sorted(missing)}"
    )
