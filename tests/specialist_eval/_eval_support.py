"""Loaders shared by the evaluation-instance tests.

Expressions render through Ansible's own Templar, so lookup('env'), undef(), mandatory and the
roles' filters behave as they do in a converge. Resolution is lazy, as in Ansible: a variable is
templated only when an expression reads it. Nothing here reads the controller environment except
through the test's own monkeypatched values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from _group_vars import load_group_vars_all

ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "roles"
GROUP_VARS = ROOT / "inventory/group_vars"


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def role_defaults(role: str) -> dict:
    return load_yaml(ROLES / role / "defaults/main.yml")


def group_vars(group: str) -> dict:
    return load_yaml(GROUP_VARS / f"{group}.yml") or {}


def core_defaults() -> dict:
    """What group_vars/all gives every host: the empty bao_* maps, and the production converge writer."""
    core = load_group_vars_all()
    seed = load_yaml(GROUP_VARS / "all/openbao_secrets/00-seed-vars.yml")
    values = {key: core[key] for key in ("bao_local_llm_secrets", "bao_apps_secrets")}
    values["openbao_secrets_converge_writer_token"] = seed["openbao_secrets_converge_writer_token"]
    return values


def host_context(role: str, group: str | None = None) -> dict:
    """Variables an evaluation host sees: role defaults, the empty core defaults, then group_vars."""
    context = dict(role_defaults(role))
    context.update(core_defaults())
    if group:
        context.update(group_vars(group))
    return context


def render(expression: Any, variables: dict) -> Any:
    templar = Templar(loader=DataLoader())
    templar.available_variables = {
        key: trust_as_template(value) if isinstance(value, str) else value
        for key, value in variables.items()
    }
    if isinstance(expression, str):
        return templar.template(trust_as_template(expression))
    return expression
