"""Private agent DSLs: an empty-by-default list, fetched only with the bearer token."""

import ast
from pathlib import Path

import yaml
from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
AGENT_DSLS = ROOT / "roles/dify_docker/vars/agent_dsls.yml"
IMPORT_LOOP = ROOT / "roles/dify_docker/tasks/import_agent_dsls.yml"
IMPORT_ONE = ROOT / "roles/dify_docker/tasks/import_agent_dsl.yml"

FETCH_TASK = "Fetch the pinned agent DSL and verify its sha256"
PRIVATE_LOOP_TASK = "Import each private agent DSL (non-fatal, skips existing app names)"
TOKEN = "fixture-token"


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def _render(expression, **context):
    # Plain Jinja with the two Ansible filters the expressions use.
    env = Environment()
    env.filters["bool"] = bool
    env.filters["combine"] = lambda base, extra: {**base, **extra}
    return env.from_string(expression).render(context)


def test_private_list_and_token_are_empty_by_default():
    defaults = _yaml(DEFAULTS)

    assert defaults["dify_docker_private_agent_dsls"] == []
    assert defaults["dify_docker_private_agent_dsl_token"] == ""


def test_pinned_sources_file_does_not_override_supplied_private_values():
    # include_vars outranks inventory and role defaults, so defining either name
    # there would replace a value supplied at converge time.
    pins = _yaml(AGENT_DSLS)

    assert not [name for name in pins if "private" in name]
    assert all("private" not in entry for entry in pins["dify_docker_agent_dsls"])


def test_private_entries_are_marked_and_skipped_without_a_token():
    loop = _task(_yaml(IMPORT_LOOP), PRIVATE_LOOP_TASK)

    assert loop["ansible.builtin.include_tasks"] == "import_agent_dsl.yml"
    assert loop["loop_control"]["loop_var"] == "dify_docker_agent_dsl"
    assert loop["loop_control"]["label"] == "private agent DSL"
    assert "dify_docker_private_agent_dsl_token | length > 0" in loop["when"]
    assert "dify_docker_import_agent_dsls | bool" in loop["when"]

    marked = ast.literal_eval(
        _render(
            loop["loop"],
            dify_docker_private_agent_dsls=[{"name": "example", "url": "u", "sha256": "s"}],
        )
    )
    assert marked == [{"name": "example", "url": "u", "sha256": "s", "private": True}]


def test_missing_token_is_reported_instead_of_fetching_unauthenticated():
    report = _task(_yaml(IMPORT_LOOP), "Report private agent DSLs skipped for a missing token")

    assert "dify_docker_private_agent_dsls | length > 0" in report["when"]
    assert "dify_docker_private_agent_dsl_token | length == 0" in report["when"]


def test_fetch_sends_the_bearer_token_only_for_private_entries():
    fetch = _task(_yaml(IMPORT_ONE), FETCH_TASK)
    headers = fetch["ansible.builtin.get_url"]["headers"]

    private = ast.literal_eval(
        _render(
            headers,
            dify_docker_agent_dsl={"private": True},
            dify_docker_private_agent_dsl_token=TOKEN,
        )
    )
    public = ast.literal_eval(
        _render(headers, dify_docker_agent_dsl={}, dify_docker_private_agent_dsl_token=TOKEN)
    )

    assert private == {"Authorization": f"Bearer {TOKEN}"}
    assert public == {}


def test_fetch_stays_checksum_verified_and_secret_safe():
    fetch = _task(_yaml(IMPORT_ONE), FETCH_TASK)

    assert fetch["no_log"] is True
    assert fetch["ansible.builtin.get_url"]["checksum"] == "sha256:{{ dify_docker_agent_dsl.sha256 }}"
    assert fetch["ansible.builtin.get_url"]["mode"] == "0600"
