"""Catalog agent apps are published as MCP servers; their URLs live in OpenBao."""

from pathlib import Path

import yaml
from jinja2 import Environment

from _defaults import load_defaults

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "roles/dify_docker/tasks/main.yml"
PUBLISH = ROOT / "roles/dify_docker/tasks/publish_agent_mcp.yml"
NGINX = ROOT / "roles/dify_docker/templates/nginx-default.conf.j2"
INCLUDE_NAME = "Publish each catalog agent app as an MCP server"
CREATE_TASK = "Create the agent app MCP server only when the app has none"
ACTIVATE_TASK = "Activate the agent app MCP server when it is not active"
STORE_BLOCK = "Store the agent app MCP server URL in OpenBao"
STORE_TASK = "Store the agent app MCP server URL through the shared seed_generated task"
SEED_INCLUDE_TASK = "Run the shared seed_generated task for the agent app MCP server URL"
# Name-only tasks: the app name is loggable, the server code and URL are not.
NAME_ONLY_TASKS = (
    "Assert exactly one agent app matched the name",
    "Assert the agent app was found",
    "Report the agent app MCP server changes a live run would make",
    "Report a published agent app MCP server",
    SEED_INCLUDE_TASK,
)


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def _render(expression, **context):
    """Evaluate a role condition or set_fact expression against a context."""
    source = expression.strip()
    if not source.startswith("{{"):
        source = "{{ " + source + " }}"
    return Environment().from_string(source).render(**context)


def _flat(text):
    """Folded scalars keep more-indented lines; compare whitespace-folded text."""
    return " ".join(text.split())


def test_the_publish_is_included_after_the_rotation_with_three_conditions():
    tasks = _yaml(MAIN)
    includes = [task.get("ansible.builtin.include_tasks") for task in tasks]
    assert includes.index("publish_agent_mcp.yml") > includes.index("wire_rotation_webhooks.yml")
    include = _task(tasks, INCLUDE_NAME)
    assert include["ansible.builtin.include_tasks"] == "publish_agent_mcp.yml"
    assert include["when"] == [
        "dify_docker_provision_model_provider | bool",
        "dify_docker_import_agent_dsls | bool",
        "dify_docker_publish_agent_mcp | bool",
    ]
    # The loop walks the built list, so dify_docker_agent_dsl_select slices this step too.
    assert include["loop"] == "{{ dify_docker_agent_dsls | default([]) }}"
    assert include["loop_control"]["loop_var"] == "dify_docker_agent_dsl"
    assert include["vars"]["dify_docker_dsl_name"] == "{{ dify_docker_agent_dsl.name }}"
    assert include["no_log"] is True


def test_the_defaults_define_the_flag_and_the_leaf_path():
    defaults = load_defaults()

    assert defaults["dify_docker_publish_agent_mcp"] is False
    assert defaults["dify_docker_agent_mcp_path"] == (
        "{{ dify_docker_openbao_path }}/agent-mcp/{{ dify_docker_dsl_name }}"
    )


def test_the_server_is_created_only_when_the_app_has_none():
    create = _task(_yaml(PUBLISH), CREATE_TASK)
    uri = create["ansible.builtin.uri"]

    assert uri["method"] == "POST"
    assert uri["status_code"] == 201
    assert create["when"] == [
        "not ansible_check_mode",
        "dify_docker_agent_mcp_server_read.json | default({}) == {}",
    ]
    condition = create["when"][1]
    assert _render(condition, dify_docker_agent_mcp_server_read={"json": {}}) == "True"
    assert _render(condition, dify_docker_agent_mcp_server_read={"json": {"id": "x"}}) == "False"
    assert create["no_log"] is True


def test_the_server_is_activated_only_when_it_is_not_active():
    activate = _task(_yaml(PUBLISH), ACTIVATE_TASK)
    uri = activate["ansible.builtin.uri"]

    assert uri["method"] == "PUT"
    assert uri["status_code"] == 200
    assert uri["body"]["status"] == "active"
    assert activate["when"] == [
        "not ansible_check_mode",
        "(dify_docker_agent_mcp_server.status | default('')) != 'active'",
    ]
    condition = _flat(activate["when"][1])
    assert _render(condition, dify_docker_agent_mcp_server={"status": "normal"}) == "True"
    assert _render(condition, dify_docker_agent_mcp_server={}) == "True"
    assert _render(condition, dify_docker_agent_mcp_server={"status": "active"}) == "False"
    assert activate["no_log"] is True


def test_the_leaf_is_seeded_only_when_the_stored_url_is_missing_or_differs():
    tasks = _yaml(PUBLISH)
    match_task = _task(tasks, "Detect whether the stored agent app MCP server URL matches")
    block = _task(tasks, STORE_BLOCK)
    matches = match_task["ansible.builtin.set_fact"]["dify_docker_agent_mcp_url_matches"]

    stored = "https://dify.example.test/mcp/server/abc/mcp"
    assert _render(
        matches,
        dify_docker_agent_mcp_lookup={"secret": {"url": stored}},
        dify_docker_agent_mcp_url=stored,
    ) == "True"
    assert _render(
        matches,
        dify_docker_agent_mcp_lookup={"secret": {}},
        dify_docker_agent_mcp_url=stored,
    ) == "False"
    assert _render(
        matches,
        dify_docker_agent_mcp_lookup={"secret": {"url": "https://dify.example.test/mcp/server/old/mcp"}},
        dify_docker_agent_mcp_url=stored,
    ) == "False"
    assert block["when"] == [
        "not ansible_check_mode",
        "not dify_docker_agent_mcp_url_matches | bool",
    ]


def test_the_url_is_written_through_the_shared_seed_task_into_the_leaf():
    tasks = _yaml(PUBLISH)
    seed = _task(_task(tasks, STORE_BLOCK)["block"], STORE_TASK)
    include = seed["block"][0]

    assert include["ansible.builtin.include_role"]["tasks_from"] == "seed_generated"
    assert include["ansible.builtin.include_role"]["name"] == "dryvist.homelab.openbao_secrets"
    assert seed["vars"]["openbao_secrets_seed_path"] == "{{ dify_docker_agent_mcp_path }}"
    fields = seed["vars"]["openbao_secrets_seed_fields"]
    assert len(fields) == 1
    assert fields[0]["name"] == "url"
    assert fields[0]["value"] == "{{ dify_docker_agent_mcp_url }}"
    # A stored URL that differs from the seeded one must be rewritten.
    assert seed["vars"]["openbao_secrets_seed_rotate_fields"] == ["url"]
    assert seed["vars"]["openbao_secrets_seed_register"] == "dify_docker_agent_mcp_seed_result"
    assert seed["no_log"] is True


def test_the_leaf_read_matches_the_agent_app_api_key_read():
    read = _task(_yaml(PUBLISH), "Read the stored agent app MCP server URL from OpenBao")[
        "community.hashi_vault.vault_kv2_get"
    ]

    assert read["auth_method"] == "token"
    assert read["token"] == "{{ openbao_secrets_converge_writer_token }}"
    assert read["engine_mount_point"] == "{{ openbao_secrets_kv_mount }}"
    assert read["path"] == "{{ dify_docker_agent_mcp_path }}"
    assert read["validate_certs"] == "{{ dify_docker_bao_validate_certs }}"
    assert load_defaults()["dify_docker_bao_validate_certs"] is True


def test_every_task_touching_the_server_code_or_url_is_no_log():
    tasks = _yaml(PUBLISH)

    for task in tasks:
        if task["name"] in NAME_ONLY_TASKS or task["name"] == STORE_BLOCK:
            continue
        assert task.get("no_log") is True, task["name"]
    assert _task(_task(tasks, STORE_BLOCK)["block"], STORE_TASK)["no_log"] is True


def test_check_mode_reports_by_app_name_only():
    tasks = _yaml(PUBLISH)
    preview = _task(tasks, "Report the agent app MCP server changes a live run would make")
    unchanged = _task(tasks, "Report a published agent app MCP server")

    assert "ansible_check_mode" in preview["when"]
    assert preview["changed_when"] is True
    message = _flat(preview["ansible.builtin.debug"]["msg"])
    assert "{{ dify_docker_dsl_name }}" in message
    # Names only: no server code or URL value reaches the report.
    assert "server_code" not in message
    assert "dify_docker_agent_mcp_url" not in message
    assert unchanged["changed_when"] is False
    assert unchanged["when"] == "dify_docker_agent_mcp_planned_changes | length == 0"


def test_the_url_is_built_from_the_role_public_url_and_the_mcp_route():
    resolve = _task(_yaml(PUBLISH), "Resolve the agent app MCP server URL")
    expression = resolve["ansible.builtin.set_fact"]["dify_docker_agent_mcp_url"]

    assert "dify_docker_public_url" in expression
    assert "/mcp/server/" in expression
    assert "dify_docker_agent_mcp_server.server_code" in expression
    assert resolve["no_log"] is True


def test_the_app_lookup_uses_the_page_cap():
    tasks = _yaml(PUBLISH)
    page_loop = next(
        task
        for task in tasks
        if task.get("ansible.builtin.include_tasks") == "list_agent_app_page.yml"
    )

    assert page_loop["loop"] == "{{ range(1, (dify_docker_app_lookup_max_pages | int) + 1) | list }}"
    assert page_loop["loop_control"] == {"loop_var": "dify_docker_app_page"}
    assert page_loop["no_log"] is True


def test_duplicate_and_missing_apps_fail_before_the_server_is_touched():
    tasks = _yaml(PUBLISH)
    duplicate = _task(tasks, "Assert exactly one agent app matched the name")
    missing = _task(tasks, "Assert the agent app was found")
    first_api_call = tasks.index(_task(tasks, "Read the agent app MCP server"))

    assert duplicate["ansible.builtin.assert"]["that"] == [
        "dify_docker_agent_mcp_app_matches | length <= 1"
    ]
    assert "{{ dify_docker_dsl_name }}" in duplicate["ansible.builtin.assert"]["fail_msg"]
    assert missing["ansible.builtin.assert"]["that"] == ["dify_docker_agent_mcp_app_id | length > 0"]
    assert "{{ dify_docker_dsl_name }}" in missing["ansible.builtin.assert"]["fail_msg"]
    assert tasks.index(duplicate) < first_api_call
    assert tasks.index(missing) < first_api_call


def test_the_nginx_template_routes_mcp_to_the_api():
    text = NGINX.read_text(encoding="utf-8")

    assert "location /mcp {" in text
    body = text.split("location /mcp {", 1)[1].split("\n    }", 1)[0]
    assert body.strip() == "proxy_pass http://api:{{ dify_docker_api_port }};"
    # Same trimmed style as the existing API path blocks.
    assert "location /triggers {" in text and "location /v1 {" in text
