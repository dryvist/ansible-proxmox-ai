"""Catalog agent apps are published as MCP servers; their URLs live in OpenBao."""

import yaml
from jinja2 import Environment

from _agent_dsl_shared import ROOT, _yaml
from _defaults import load_defaults

MAIN = ROOT / "roles/dify_docker/tasks/main.yml"
PUBLISH = ROOT / "roles/dify_docker/tasks/publish_agent_mcp.yml"
RESOLVE = ROOT / "roles/dify_docker/tasks/resolve_agent_app_id.yml"
READ = ROOT / "roles/dify_docker/tasks/read_agent_leaf.yml"
SEED = ROOT / "roles/dify_docker/tasks/seed_agent_leaf.yml"
NGINX = ROOT / "roles/dify_docker/templates/nginx-default.conf.j2"
INCLUDE_NAME = "Publish each catalog agent app as an MCP server"
CREATE_TASK = "Create the agent app MCP server only when the app has none"
ACTIVATE_TASK = "Activate the agent app MCP server when it is not active"
STORE_BLOCK = "Store the agent app MCP server URL in OpenBao"
STORE_TASK = "Store the agent app MCP server URL through the shared seed_generated task"
RESOLVE_INCLUDE_TASK = "Resolve the agent app id through the shared app lookup"
# Name-only tasks: the app name is loggable, the server code and URL are not.
NAME_ONLY_TASKS = (
    "Report the agent app MCP server changes a live run would make",
    "Report a published agent app MCP server",
)


def _task(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def _render(expression, **context):
    """Evaluate a role condition or set_fact expression against a context."""
    source = expression.strip()
    if not source.startswith("{{"):
        source = "{{ " + source + " }}"
    env = Environment()
    env.filters["bool"] = bool
    return env.from_string(source).render(**context)


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

    assert seed["ansible.builtin.include_tasks"] == "seed_agent_leaf.yml"
    assert seed["vars"]["dify_docker_agent_leaf_seed_path"] == "{{ dify_docker_agent_mcp_path }}"
    fields = seed["vars"]["dify_docker_agent_leaf_seed_fields"]
    assert len(fields) == 1
    assert fields[0]["name"] == "url"
    assert fields[0]["value"] == "{{ dify_docker_agent_mcp_url }}"
    # A stored URL that differs from the seeded one must be rewritten.
    assert seed["vars"]["dify_docker_agent_leaf_seed_rotate_fields"] == ["url"]
    assert seed["vars"]["dify_docker_agent_leaf_seed_register"] == "dify_docker_agent_mcp_seed_result"
    assert seed["no_log"] is True

    seed_block = next(task for task in _yaml(SEED) if "block" in task)
    seed_task = seed_block["block"][0]
    include = seed_task["block"][0]
    assert include["ansible.builtin.include_role"]["tasks_from"] == "seed_generated"
    assert include["ansible.builtin.include_role"]["name"] == "dryvist.homelab.openbao_secrets"
    assert seed_task["vars"]["openbao_secrets_seed_path"] == "{{ dify_docker_agent_leaf_seed_path }}"
    assert seed_task["no_log"] is True


def test_the_leaf_read_matches_the_agent_app_api_key_read():
    read = next(
        task for task in _yaml(READ) if "community.hashi_vault.vault_kv2_get" in task
    )["community.hashi_vault.vault_kv2_get"]

    assert read["auth_method"] == "token"
    assert read["token"] == "{{ openbao_secrets_converge_writer_token }}"
    assert read["engine_mount_point"] == "{{ openbao_secrets_kv_mount }}"
    assert read["path"] == "{{ dify_docker_agent_leaf_read_path }}"
    assert read["validate_certs"] == "{{ dify_docker_bao_validate_certs }}"
    assert load_defaults()["dify_docker_bao_validate_certs"] is True

    caller = _task(_yaml(PUBLISH), "Read the stored agent app MCP server URL from OpenBao")
    assert caller["vars"]["dify_docker_agent_leaf_read_path"] == "{{ dify_docker_agent_mcp_path }}"
    assert caller["vars"]["dify_docker_agent_leaf_read_register"] == "dify_docker_agent_mcp_lookup"


def test_every_task_touching_the_server_code_or_url_is_no_log():
    tasks = _yaml(PUBLISH)

    for task in tasks:
        if task["name"] in NAME_ONLY_TASKS or task["name"] == STORE_BLOCK:
            continue
        assert task.get("no_log") is True, task["name"]
    assert _task(_task(tasks, STORE_BLOCK)["block"], STORE_TASK)["no_log"] is True

    # The shared helpers no_log every task that receives, holds or writes the
    # server code or URL; the resolve asserts stay loggable on app names only.
    assert all(task["no_log"] is True for task in _yaml(READ))
    seed_block = next(task for task in _yaml(SEED) if "block" in task)
    assert seed_block["block"][0]["no_log"] is True
    assert all(task["no_log"] is True for task in seed_block["rescue"])


def test_check_mode_plans_activation_only_for_an_existing_server():
    # Dify 1.18 creates a new MCP server active, so a live run never activates one it just created.
    planned = _task(_yaml(PUBLISH), "Detect whether the agent app MCP server needs changes")[
        "ansible.builtin.set_fact"
    ]["dify_docker_agent_mcp_planned_changes"]

    def plan(read_json, url_matches=False):
        return yaml.safe_load(
            _render(
                planned,
                dify_docker_agent_mcp_server_read={"json": read_json},
                dify_docker_agent_mcp_server=read_json,
                dify_docker_agent_mcp_url_matches=url_matches,
            )
        )

    assert plan({}) == ["create its MCP server", "store its MCP server URL"]
    assert plan({"id": "s1", "status": "inactive"}, url_matches=True) == ["activate its MCP server"]
    assert plan({"id": "s1", "status": "active"}, url_matches=True) == []


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
    resolve_include = _task(tasks, RESOLVE_INCLUDE_TASK)
    assert resolve_include["ansible.builtin.include_tasks"] == "resolve_agent_app_id.yml"
    assert resolve_include["no_log"] is True

    page_loop = _task(_yaml(RESOLVE), "List Dify apps page by page until no more pages remain")
    assert page_loop["loop"] == "{{ range(1, (dify_docker_app_lookup_max_pages | int) + 1) | list }}"
    assert page_loop["loop_control"] == {"loop_var": "dify_docker_app_page"}
    assert page_loop["no_log"] is True


def test_duplicate_and_missing_apps_fail_before_the_server_is_touched():
    tasks = _yaml(PUBLISH)
    resolve_tasks = _yaml(RESOLVE)
    duplicate = _task(resolve_tasks, "Assert exactly one Dify agent app matched the name")
    missing = _task(resolve_tasks, "Assert the Dify agent app was found")
    first_api_call = tasks.index(_task(tasks, "Read the agent app MCP server"))

    assert duplicate["ansible.builtin.assert"]["that"] == [
        "dify_docker_agent_app_matches | length <= 1"
    ]
    assert "{{ dify_docker_dsl_name }}" in duplicate["ansible.builtin.assert"]["fail_msg"]
    assert missing["ansible.builtin.assert"]["that"] == ["dify_docker_agent_app_id | length > 0"]
    assert "{{ dify_docker_dsl_name }}" in missing["ansible.builtin.assert"]["fail_msg"]
    # The shared asserts run inside the lookup, which the publish includes
    # before its first server API call.
    assert tasks.index(_task(tasks, RESOLVE_INCLUDE_TASK)) < first_api_call


def test_the_nginx_template_routes_mcp_to_the_api():
    text = NGINX.read_text(encoding="utf-8")

    assert "location /mcp {" in text
    body = text.split("location /mcp {", 1)[1].split("\n    }", 1)[0]
    assert "proxy_pass http://api:{{ dify_docker_api_port }};" in body
    # Same trimmed style as the existing API path blocks.
    assert "location /triggers {" in text and "location /v1 {" in text


def _includes(items):
    """Collect include_tasks targets from every nesting level of a task list."""
    for task in items or []:
        if not isinstance(task, dict):
            continue
        if "ansible.builtin.include_tasks" in task:
            yield task["ansible.builtin.include_tasks"]
        for section in ("block", "rescue", "always"):
            yield from _includes(task.get(section))


def test_both_callers_share_the_helpers_and_mcp_access_log_is_off():
    task_dir = ROOT / "roles/dify_docker/tasks"
    helpers = ("resolve_agent_app_id.yml", "read_agent_leaf.yml", "seed_agent_leaf.yml")

    for caller in ("ensure_agent_app_api_key.yml", "publish_agent_mcp.yml"):
        tasks = _yaml(task_dir / caller)
        includes = set(_includes(tasks))
        for helper in helpers:
            assert helper in includes, (caller, helper)

    # The server code in the request path is a bearer secret; nginx must not
    # log any request to it.
    body = NGINX.read_text(encoding="utf-8").split("location /mcp {", 1)[1].split("\n    }", 1)[0]
    assert "access_log off;" in body
