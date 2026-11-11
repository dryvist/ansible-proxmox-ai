"""Paths, constants and helpers shared by the declared catalog app variable tests."""

import ast
import json
import re
from pathlib import Path

import yaml
from jinja2 import Environment

from _agent_dsl_shared import IMPORT_LOOP, IMPORT_ONE
from _defaults import load_defaults


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/dify_docker/tasks"
RECONCILE = TASKS / "import_agent_dsl_reconcile.yml"
PRE = TASKS / "import_agent_dsl_variables.yml"
ONE = TASKS / "import_agent_dsl_variable_one.yml"
APPLY = TASKS / "import_agent_dsl_variables_apply.yml"
PRUNE = TASKS / "import_agent_dsl_variables_prune.yml"
CONFIG = TASKS / "import_agent_dsl_config_leaf.yml"
DERIVE = TASKS / "import_agent_dsl_derive.yml"
PROVIDER = TASKS / "import_agent_dsl_provider_nodes.yml"
EDITED = (IMPORT_ONE, IMPORT_LOOP, RECONCILE, PRE, ONE, APPLY, PRUNE, CONFIG, DERIVE, PROVIDER)
LEAF_INCLUDE = "Read the per-app declared config from its leaf when the flag is set"
LEAF_RESET = "Reset the per-app leaf declarations before this app"
DERIVE_TASK = "Derive the declared-variable facts for this app"
SELECT_STALE = "Select the older accessors of this app to destroy, keeping the newest one not minted in this run"
RECORD = "Record each minted secret id accessor for this run's prune protection"

RESOLVE = "Resolve declared app variables and tool provider ids before any import"
WRITE = "Write the declared app variables to the draft before publishing"
CLEAR = "Clear the declared app variable values from facts"
PRUNE_INCLUDE = "Prune older AppRole secret ids once the declared variables are published"
DECLARED_PUBLISH = "Publish the declared app variables for an unchanged catalog app"
NEW_TOP_LEVEL = {RESOLVE, WRITE, CLEAR, PRUNE_INCLUDE, DECLARED_PUBLISH, LEAF_RESET, LEAF_INCLUDE, DERIVE_TASK}

# Facts that hold a resolved value or a raw API response. Names-only facts are not listed,
# so the assert tasks that print a name stay visible.
SENSITIVE = (
    "dify_docker_dsl_variable_entries",
    "dify_docker_dsl_variable_values",
    "dify_docker_dsl_env_patch",
    "dify_docker_dsl_kv_read",
    "dify_docker_dsl_role_id_read",
    "dify_docker_dsl_secret_id_ages",
    "dify_docker_dsl_secret_id_stale",
    "dify_docker_dsl_secret_id_list",
    "dify_docker_dsl_secret_id_lookups",
    "dify_docker_dsl_secret_id_mints",
    "dify_docker_dsl_draft",
    "dify_docker_dsl_tool_providers",
    "dify_docker_dsl_tool_ids",
    "dify_docker_dsl_minted_accessors",
    "dify_docker_dsl_config_leaf",
    "dify_docker_dsl_config_variables",
    "dify_docker_dsl_config_tool_providers",
    "dify_docker_dsl_mcp_nodes",
    "dify_docker_dsl_mcp_provider_ids",
    "dify_docker_dsl_workspace_ids",
    "dify_docker_agent_leaf_read_result",
    "openbao_secrets_converge_writer_token",
)


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def _fact(tasks, name, fact):
    return _task(tasks, name)["ansible.builtin.set_fact"][fact]


def _env():
    environment = Environment()
    environment.filters["difference"] = lambda left, right: [item for item in left if item not in right]
    environment.filters["to_uuid"] = lambda text: f"uuid:{text}"
    environment.filters["regex_replace"] = lambda text, pattern, repl: re.sub(pattern, repl, text)
    environment.filters["regex_findall"] = lambda text, pattern: re.findall(pattern, text)
    environment.filters["from_json"] = json.loads
    environment.filters["to_json"] = json.dumps
    environment.filters["dict2items"] = lambda mapping: [{"key": key, "value": value}
                                                         for key, value in mapping.items()]
    environment.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
    environment.tests["search"] = lambda value, pattern: re.search(pattern, value) is not None
    environment.filters["combine"] = lambda base, extra: {**base, **extra}
    environment.filters["bool"] = lambda value: value is True or str(value).lower() in (
        "true", "yes", "on", "1"
    )
    return environment


def _render(expression, **context):
    return ast.literal_eval(_env().from_string(expression).render(**context))


def _passes(conditions, **context):
    return all(
        _env().from_string("{{ " + condition + " }}").render(**context).strip() == "True"
        for condition in conditions
    )
