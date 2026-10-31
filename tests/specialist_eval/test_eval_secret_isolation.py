"""An evaluation instance of Dify or LangFlow never resolves a production value.

Each check renders the expression a role renders, for an evaluation host's variables. Production
markers sit where production sources put them: the unprefixed env names, and the Phase 0 prefetch
maps (injected here even though an evaluation host never receives them). The guest's own key and
the DIFY_EVAL_* env names carry the other markers. Every credential also has a control: the same
expression without the evaluation override must select the production marker. A check that could
never fail is caught by its control.
"""

from __future__ import annotations

import re

import pytest
from ansible.errors import AnsibleUndefinedVariable

from _eval_support import (
    DIFY_EVAL_PATH,
    EVALUATION,
    GROUP_VARS,
    GUEST,
    LANGFLOW_EVAL_PATH,
    PRODUCTION,
    PRODUCTION_PREFETCH,
    ROLES,
    group_vars,
    guest_key_facts,
    host_context,
    load_yaml,
    render,
    role_defaults,
    task_named,
)

DIFY_TASK_FILE = "storage_and_secret.yml"
DIFY_CALLER = "Resolve the caller SECRET_KEY (secret store or environment, then on-guest file)"
DIFY_SEED = "Seed the Dify SECRET_KEY through the shared seed_generated task"
LANGFLOW_CALLER = "Resolve the caller LANGFLOW_SECRET_KEY (inventory override, then on-guest file)"
LANGFLOW_SEED = "Seed the LangFlow secrets through the shared seed_generated task"
ENV_LOOKUP = re.compile(r"lookup\('env', '([A-Z0-9_]+)'\)")
# A DNS suffix that names the public URL. It is not a credential, so no override is needed.
NON_CREDENTIAL_ENV = {"PROXMOX_SUBDOMAIN"}
PRODUCTION_WRITER_ENV = "OPENBAO_SEED_WRITER_TOKEN"

DIFY_ENV_CREDENTIALS = [
    # (role variable, production env name, evaluation env name)
    ("dify_docker_db_password", "DIFY_DB_PASSWORD", "DIFY_EVAL_DB_PASSWORD"),
    ("dify_docker_redis_password", "DIFY_REDIS_PASSWORD", "DIFY_EVAL_REDIS_PASSWORD"),
    ("dify_docker_sandbox_api_key", "DIFY_SANDBOX_API_KEY", "DIFY_EVAL_SANDBOX_API_KEY"),
    ("dify_docker_weaviate_api_key", "DIFY_WEAVIATE_API_KEY", "DIFY_EVAL_WEAVIATE_API_KEY"),
    ("dify_docker_plugin_daemon_key", "DIFY_PLUGIN_DAEMON_KEY", "DIFY_EVAL_PLUGIN_DAEMON_KEY"),
    ("dify_docker_inner_api_key", "DIFY_INNER_API_KEY", "DIFY_EVAL_INNER_API_KEY"),
    ("dify_docker_admin_email", "DIFY_ADMIN_EMAIL", "DIFY_EVAL_ADMIN_EMAIL"),
    ("dify_docker_admin_password", "DIFY_ADMIN_PASSWORD", "DIFY_EVAL_ADMIN_PASSWORD"),
]
DIFY_IDS = [variable for variable, _, _ in DIFY_ENV_CREDENTIALS]

# The evaluation override sets each of these to "", so the seed generates the value in the eval path.
LANGFLOW_CALLER_VARIABLES = [
    ("langflow_docker_superuser", "LANGFLOW_SUPERUSER"),
    ("langflow_docker_superuser_password", "LANGFLOW_SUPERUSER_PASSWORD"),
    ("langflow_docker_db_password", "LANGFLOW_DB_PASSWORD"),
]
LANGFLOW_IDS = [variable for variable, _ in LANGFLOW_CALLER_VARIABLES]

PREFETCH_MODES = [None, PRODUCTION_PREFETCH]
PREFETCH_IDS = ["real-context", "production-maps-injected"]


def _render_caller_dify(context: dict) -> str:
    expression = task_named("dify_docker", DIFY_TASK_FILE, DIFY_CALLER)["ansible.builtin.set_fact"]
    return render(expression["dify_docker_secret_key_caller"], context)


def _render_caller_langflow(context: dict) -> str:
    expression = task_named("langflow_docker", "main.yml", LANGFLOW_CALLER)["ansible.builtin.set_fact"]
    return render(expression["langflow_docker_secret_key_caller"], context)


def _env_names(value: object) -> set[str]:
    return set(ENV_LOOKUP.findall(str(value)))


@pytest.mark.parametrize(("variable", "production_env", "evaluation_env"), DIFY_ENV_CREDENTIALS, ids=DIFY_IDS)
def test_dify_credential_resolves_to_its_evaluation_value(
    variable: str, production_env: str, evaluation_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(production_env, PRODUCTION)
    monkeypatch.setenv(evaluation_env, EVALUATION)
    context = host_context("dify_docker", "dify_eval_group", PRODUCTION_PREFETCH)

    assert render("{{ " + variable + " }}", context) == EVALUATION


@pytest.mark.parametrize(("variable", "production_env", "evaluation_env"), DIFY_ENV_CREDENTIALS, ids=DIFY_IDS)
def test_dify_credential_fails_closed_without_its_evaluation_value(
    variable: str, production_env: str, evaluation_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(production_env, PRODUCTION)
    monkeypatch.delenv(evaluation_env, raising=False)
    context = host_context("dify_docker", "dify_eval_group", PRODUCTION_PREFETCH)

    with pytest.raises(AnsibleUndefinedVariable):
        render("{{ " + variable + " }}", context)


@pytest.mark.parametrize(("variable", "production_env", "evaluation_env"), DIFY_ENV_CREDENTIALS, ids=DIFY_IDS)
def test_dify_credential_control_selects_production_without_the_override(
    variable: str, production_env: str, evaluation_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(production_env, PRODUCTION)
    context = host_context("dify_docker")

    assert render("{{ " + variable + " }}", context) == PRODUCTION


def test_dify_model_key_resolves_to_its_evaluation_router_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIFY_EVAL_MODEL_API_KEY", EVALUATION)
    context = host_context("dify_docker", "dify_eval_group", PRODUCTION_PREFETCH)

    assert render("{{ dify_docker_model_api_key }}", context) == EVALUATION


def test_dify_model_key_fails_closed_without_an_evaluation_router_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DIFY_EVAL_MODEL_API_KEY", raising=False)
    context = host_context("dify_docker", "dify_eval_group", PRODUCTION_PREFETCH)

    with pytest.raises(AnsibleUndefinedVariable):
        render("{{ dify_docker_model_api_key }}", context)


def test_dify_model_key_control_takes_the_production_router_key_without_the_override() -> None:
    context = host_context("dify_docker", None, PRODUCTION_PREFETCH)

    assert render("{{ dify_docker_model_api_key }}", context) == PRODUCTION


@pytest.mark.parametrize("prefetch", PREFETCH_MODES, ids=PREFETCH_IDS)
def test_dify_secret_key_keeps_the_guest_key_and_never_the_production_key(
    prefetch: dict | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DIFY_SECRET_KEY", PRODUCTION)
    context = {**host_context("dify_docker", "dify_eval_group", prefetch), **guest_key_facts()}

    assert _render_caller_dify(context) == GUEST


def test_dify_secret_key_control_production_group_selects_the_production_key() -> None:
    context = {**host_context("dify_docker", "dify_group", PRODUCTION_PREFETCH), **guest_key_facts()}

    assert _render_caller_dify(context) == PRODUCTION


def test_dify_seed_writes_the_guest_key_to_the_evaluation_path_only() -> None:
    context = {**host_context("dify_docker", "dify_eval_group", PRODUCTION_PREFETCH), **guest_key_facts()}
    context["dify_docker_secret_key_caller"] = _render_caller_dify(context)
    seed_vars = task_named("dify_docker", DIFY_TASK_FILE, DIFY_SEED)["block"][0]["vars"]

    assert render(seed_vars["openbao_secrets_seed_path"], context) == DIFY_EVAL_PATH
    assert DIFY_EVAL_PATH != "ai/dify"
    assert [render(field["value"], context) for field in seed_vars["openbao_secrets_seed_fields"]] == [GUEST]


@pytest.mark.parametrize(("variable", "production_env"), LANGFLOW_CALLER_VARIABLES, ids=LANGFLOW_IDS)
def test_langflow_caller_value_is_empty_so_the_seed_generates_it(
    variable: str, production_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(production_env, PRODUCTION)
    context = host_context("langflow_docker", "langflow_eval_group", PRODUCTION_PREFETCH)

    assert render("{{ " + variable + " }}", context) == ""


@pytest.mark.parametrize(("variable", "production_env"), LANGFLOW_CALLER_VARIABLES, ids=LANGFLOW_IDS)
def test_langflow_control_selects_production_without_the_override(
    variable: str, production_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(production_env, PRODUCTION)
    context = host_context("langflow_docker")

    assert render("{{ " + variable + " }}", context) == PRODUCTION


@pytest.mark.parametrize("prefetch", PREFETCH_MODES, ids=PREFETCH_IDS)
def test_langflow_secret_key_keeps_the_guest_key(prefetch: dict | None) -> None:
    context = {**host_context("langflow_docker", "langflow_eval_group", prefetch), **guest_key_facts()}

    assert _render_caller_langflow(context) == GUEST


def test_langflow_seed_writes_only_evaluation_values_to_the_evaluation_path(monkeypatch: pytest.MonkeyPatch) -> None:
    for _, production_env in LANGFLOW_CALLER_VARIABLES:
        monkeypatch.setenv(production_env, PRODUCTION)
    context = {**host_context("langflow_docker", "langflow_eval_group", PRODUCTION_PREFETCH), **guest_key_facts()}
    context["langflow_docker_secret_key_caller"] = _render_caller_langflow(context)
    seed_vars = task_named("langflow_docker", "main.yml", LANGFLOW_SEED)["block"][0]["vars"]
    rendered = {
        field["name"]: render(field["value"], context) for field in seed_vars["openbao_secrets_seed_fields"]
    }

    assert render(seed_vars["openbao_secrets_seed_path"], context) == LANGFLOW_EVAL_PATH
    assert rendered == {
        "LANGFLOW_SUPERUSER": "",
        "LANGFLOW_SUPERUSER_PASSWORD": "",
        "LANGFLOW_DB_PASSWORD": "",
        "LANGFLOW_SECRET_KEY": GUEST,
    }


def test_every_env_backed_dify_variable_is_a_listed_credential_with_an_evaluation_override() -> None:
    defaults = role_defaults("dify_docker")
    env_backed = {variable for variable, value in defaults.items() if _env_names(value) - NON_CREDENTIAL_ENV}
    bao_backed = {variable for variable, value in defaults.items() if "bao_" in str(value)}
    group_sourced = set(group_vars("dify_group"))
    overrides = set(group_vars("dify_eval_group"))

    assert env_backed == set(DIFY_IDS)
    assert bao_backed == {"dify_docker_model_api_key"}
    assert group_sourced == {"dify_docker_secret_key"}
    assert env_backed | bao_backed | group_sourced <= overrides


def test_dify_evaluation_overrides_read_only_evaluation_names_and_no_production_map() -> None:
    overrides = group_vars("dify_eval_group")
    env_names = set().union(*(_env_names(value) for value in overrides.values()))

    assert env_names == {evaluation_env for _, _, evaluation_env in DIFY_ENV_CREDENTIALS} | {"DIFY_EVAL_MODEL_API_KEY"}
    for value in overrides.values():
        assert "bao_local_llm_secrets" not in str(value)
        assert "bao_apps_secrets" not in str(value)


def test_every_production_sourced_langflow_variable_has_an_evaluation_override() -> None:
    defaults = role_defaults("langflow_docker")
    env_backed = {variable for variable, value in defaults.items() if _env_names(value)}
    overrides = set(group_vars("langflow_eval_group"))

    assert env_backed == {variable for variable, _ in LANGFLOW_CALLER_VARIABLES}
    # The inventory can override the LangFlow SECRET_KEY, so it is production-sourced too.
    assert env_backed | {"langflow_docker_secret_key"} <= overrides
    langflow_overrides = group_vars("langflow_eval_group")
    assert set().union(*(_env_names(value) for value in langflow_overrides.values())) == set()
    for value in langflow_overrides.values():
        assert "bao_local_llm_secrets" not in str(value)
        assert "bao_apps_secrets" not in str(value)


SEED_WRITER_TARGETS = [
    pytest.param("dify_docker", "dify_eval_group", DIFY_TASK_FILE, DIFY_SEED, id="dify"),
    pytest.param("langflow_docker", "langflow_eval_group", "main.yml", LANGFLOW_SEED, id="langflow"),
]


@pytest.mark.parametrize(("role", "group", "task_file", "seed_name"), SEED_WRITER_TARGETS)
def test_seed_uses_the_standard_converge_writer(
    role: str, group: str, task_file: str, seed_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seed writes with the converge writer sim_docker uses. No evaluation group overrides it, so the
    ansible-converge policy is the write boundary, and the role's own path (checked above) limits the target."""
    monkeypatch.setenv(PRODUCTION_WRITER_ENV, PRODUCTION)
    context = host_context(role, group, PRODUCTION_PREFETCH)
    seed_vars = task_named(role, task_file, seed_name)["block"][0]["vars"]

    assert "openbao_secrets_converge_writer_token" not in group_vars(group)
    assert render(seed_vars["openbao_secrets_seed_token"], context) == PRODUCTION


GRANTED_EVALUATION_LEAVES = {f"ai/eval/specialist-platform/{leaf}" for leaf in ("sim", "langflow", "dify", "hermes")}


def test_every_evaluation_path_is_a_leaf_the_policy_grants() -> None:
    """The ASM converge policy grants exactly these four leaves. An evaluation path outside them is refused."""
    sim_path = load_yaml(ROLES / "sim_docker/defaults/main.yml")["sim_docker_openbao_path"]

    assert {DIFY_EVAL_PATH, LANGFLOW_EVAL_PATH, sim_path} <= GRANTED_EVALUATION_LEAVES
    assert len(GRANTED_EVALUATION_LEAVES) == 4


def _domain_strings() -> set[str]:
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            found.add(node)

    for name in ("10-domains.yml", "10-domains-identities.yml"):
        walk(load_yaml(GROUP_VARS / "all/openbao_secrets" / name))
    return found


def test_production_prefetch_reads_the_production_app_paths() -> None:
    assert {"ai/dify", "ai/langflow"} <= _domain_strings()


@pytest.mark.parametrize("evaluation_path", [DIFY_EVAL_PATH, LANGFLOW_EVAL_PATH])
def test_no_production_domain_path_covers_the_evaluation_namespace(evaluation_path: str) -> None:
    for domain_path in _domain_strings():
        assert domain_path != evaluation_path, domain_path
        assert not evaluation_path.startswith(domain_path + "/"), domain_path


def _seed_paths(role: str) -> list[str]:
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "openbao_secrets_seed_path":
                    found.append(value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for file in sorted((ROLES / role / "tasks").glob("*.yml")):
        walk(load_yaml(file))
    return found


@pytest.mark.parametrize(
    ("role", "group", "expected"),
    [("dify_docker", "dify_eval_group", DIFY_EVAL_PATH), ("langflow_docker", "langflow_eval_group", LANGFLOW_EVAL_PATH)],
)
def test_every_seed_path_in_the_role_renders_to_the_evaluation_path(role: str, group: str, expected: str) -> None:
    paths = _seed_paths(role)
    context = host_context(role, group, PRODUCTION_PREFETCH)

    assert paths
    assert {render(path, context) for path in paths} == {expected}
