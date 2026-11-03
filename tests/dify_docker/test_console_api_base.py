"""The Dify console API base is asserted non-empty and renders the container address."""

from _admin_secret_shared import (
    CONSOLE_BASE_ASSERT_TASK,
    DEFAULTS,
    EVAL_GROUP,
    GROUP_VARS,
    load_yaml,
    render,
    render_all,
    task_named,
)


def test_console_api_base_assert_requires_a_non_empty_value():
    task = task_named(CONSOLE_BASE_ASSERT_TASK)

    context = {
        **load_yaml(DEFAULTS),
        "bao_local_llm_secrets": {},
        "group_names": [],
    }

    unset = dict(context)
    assert not all(render_all(task["ansible.builtin.assert"]["that"], unset))

    empty = {**context, "dify_docker_console_api_base": ""}
    assert not all(render_all(task["ansible.builtin.assert"]["that"], empty))

    set_value = {
        **context,
        "dify_docker_console_api_base": "http://dify.example.test:8000/console/api",
    }
    assert all(render_all(task["ansible.builtin.assert"]["that"], set_value))


def console_base_context(group_vars, container_ip):
    return {
        **load_yaml(DEFAULTS),
        **load_yaml(group_vars),
        "container_ip": container_ip,
        "hostvars": {
            "localhost": {
                "tofu_data": {"constants": {"service_ports": {"dify_web": "8000"}}}
            }
        },
    }


def test_group_vars_console_api_base_renders_the_container_address_and_web_port():
    container_ip = "dify.example.test"

    assert render(
        "{{ dify_docker_console_api_base }}",
        console_base_context(GROUP_VARS, container_ip),
    ) == f"http://{container_ip}:8000/console/api"


def test_eval_group_console_api_base_renders_the_container_address_and_web_port():
    container_ip = "dify-eval.example.test"

    assert render(
        "{{ dify_docker_console_api_base }}",
        console_base_context(EVAL_GROUP, container_ip),
    ) == f"http://{container_ip}:8000/console/api"
