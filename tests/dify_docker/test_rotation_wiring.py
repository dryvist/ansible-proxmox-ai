"""The tier2 rotation wiring: derived agents, patch scope, check mode, secrets."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WIRING = ROOT / "roles/dify_docker/tasks/wire_rotation_webhooks.yml"
AGENT = ROOT / "roles/dify_docker/tasks/wire_rotation_agent.yml"
MAIN = ROOT / "roles/dify_docker/tasks/main.yml"
DEFAULTS = ROOT / "roles/dify_docker/defaults/main/20-agent-apps.yml"

OPERATOR_KNOBS = ("ROTATION_SPEED", "ROTATION_OFFSET", "ROTATION_PAUSED")
WRITE_GATED_MODULES = ("ansible.builtin.uri",)


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dump(path):
    return yaml.safe_dump(_yaml(path), width=1000)


def _task(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def test_wiring_is_included_after_the_agent_dsl_import():
    tasks = _yaml(MAIN)
    includes = [task.get("ansible.builtin.include_tasks") for task in tasks]

    assert "wire_rotation_webhooks.yml" in includes
    assert includes.index("wire_rotation_webhooks.yml") > includes.index("import_agent_dsls.yml")
    wiring = _task(tasks, "Wire the tier2 rotation to each agent's webhook trigger")
    assert wiring["ansible.builtin.include_tasks"] == "wire_rotation_webhooks.yml"
    assert wiring["when"] == [
        "dify_docker_provision_model_provider | bool",
        "dify_docker_import_agent_dsls | bool",
    ]


def test_agent_set_is_derived_from_the_rotation_draft_variables():
    wiring_tasks = _yaml(WIRING)
    id_keys = _task(wiring_tasks, "Collect the rotation draft webhook id keys")[
        "ansible.builtin.set_fact"
    ]
    agent_keys = _task(wiring_tasks, "Derive the rotation agent keys from paired draft variables")[
        "ansible.builtin.set_fact"
    ]

    assert "select('match', '^[A-Z0-9_]+_WEBHOOK_ID$')" in (
        id_keys["dify_docker_rotation_webhook_id_keys"]
    )
    trigger_expression = agent_keys["dify_docker_rotation_agent_keys"]
    assert "select('match', '^[A-Z0-9_]+_TRIGGER_URL$')" in trigger_expression
    assert "select('in', dify_docker_rotation_webhook_id_keys)" in trigger_expression

    mapping = _task(_yaml(AGENT), "Map the rotation agent key to its Dify app name")
    assert mapping["ansible.builtin.set_fact"]["dify_docker_rotation_agent_app_name"].strip() == (
        "{{ dify_docker_rotation_agent_key | lower | regex_replace('_', '-') }}"
    )
    # No hard-coded agent list: the catalog example name never appears.
    assert "AI_NEWS" not in _dump(WIRING)
    assert "AI_NEWS" not in _dump(AGENT)
    assert _yaml(DEFAULTS)["dify_docker_rotation_app_name"] == "tier2-rotation"


def test_patch_carries_only_the_trigger_url_and_webhook_id_variables():
    tasks = _yaml(AGENT)
    resolve = _task(tasks, "Resolve the agent draft variables for this key")
    resolve_names = str(resolve["ansible.builtin.set_fact"])
    assert resolve_names.count("selectattr('name', 'equalto'") == 2
    assert "dify_docker_rotation_agent_key ~ '_TRIGGER_URL'" in resolve_names
    assert "dify_docker_rotation_agent_key ~ '_WEBHOOK_ID'" in resolve_names

    accumulate = _task(
        tasks, "Add the agent draft variables to the rotation patch when the wiring changed"
    )
    patch_expression = accumulate["ansible.builtin.set_fact"]["dify_docker_rotation_patch_variables"]
    assert patch_expression.count("combine({'value':") == 2
    assert "dify_docker_rotation_agent_trigger_url_var[0]" in patch_expression
    assert "dify_docker_rotation_agent_webhook_id_var[0]" in patch_expression
    assert "dify_docker_rotation_agent_webhook.json.webhook_id" in patch_expression

    wiring_tasks = _yaml(WIRING)
    sync = _task(wiring_tasks, "Sync the rotation draft environment variables")
    body = sync["ansible.builtin.uri"]["body"]
    assert body["graph"] == "{{ dify_docker_rotation_draft.json.graph }}"
    assert body["features"] == "{{ dify_docker_rotation_draft.json.features }}"
    assert body["hash"] == "{{ dify_docker_rotation_draft.json.hash }}"
    patch = body["environment_variable_patch"]
    assert patch["environment_variables"] == "{{ dify_docker_rotation_patch_variables }}"
    assert patch["deleted_environment_variable_ids"] == []


def test_operator_knobs_are_never_wired():
    for path in (WIRING, AGENT):
        text = path.read_text(encoding="utf-8")
        for knob in OPERATOR_KNOBS:
            assert knob not in text, (path.name, knob)
            assert knob.lower() not in text, (path.name, knob)


def test_wiring_is_idempotent_and_enables_triggers_only_when_disabled():
    wiring_tasks = _yaml(WIRING)
    agent_tasks = _yaml(AGENT)

    write_task_names = (
        "Sync the rotation draft environment variables",
        "Publish the rotation workflow with the wired webhooks",
    )
    for name in write_task_names:
        task = _task(wiring_tasks, name)
        assert "not ansible_check_mode" in task["when"]
        assert "dify_docker_rotation_patch_variables | length > 0" in task["when"]

    detection = _task(agent_tasks, "Detect whether the agent draft variables need the wiring")
    detection_expression = str(detection["ansible.builtin.set_fact"])
    assert "dify_docker_trigger_url" in detection_expression
    assert "regex_replace('/+$', '')" in detection_expression
    assert "| length == 0" in detection_expression

    rotation_enable = _task(wiring_tasks, "Enable the rotation schedule trigger when it is disabled")
    agent_enable = _task(agent_tasks, "Enable the agent webhook trigger when it is disabled")
    for enable_task in (rotation_enable, agent_enable):
        assert "not ansible_check_mode" in enable_task["when"]
        assert "status | default('') != 'enabled'" in str(enable_task["when"])
        assert enable_task["ansible.builtin.uri"]["body"]["enable_trigger"] is True
    assert rotation_enable["ansible.builtin.uri"]["body"]["trigger_id"] == (
        "{{ dify_docker_rotation_schedule_triggers[0].id }}"
    )
    assert agent_enable["ansible.builtin.uri"]["body"]["trigger_id"] == (
        "{{ dify_docker_rotation_agent_webhook_triggers[0].id }}"
    )


def test_check_mode_writes_nothing_and_previews_only_variable_names():
    read_only_methods = ("GET", "HEAD", "OPTIONS")
    for path in (WIRING, AGENT):
        for task in _yaml(path):
            uri = task.get("ansible.builtin.uri", {})
            if uri and uri.get("method") not in read_only_methods:
                assert "not ansible_check_mode" in task.get("when", []), task["name"]
            # The wiring files only read and call the console API; no module
            # writes to the target.
            assert not any(
                key.startswith("ansible.") and key not in WRITE_GATED_MODULES
                and key not in {
                    "ansible.builtin.debug",
                    "ansible.builtin.include_tasks",
                    "ansible.builtin.set_fact",
                    "ansible.builtin.assert",
                }
                for key in task
            ), task["name"]

    wiring_tasks = _yaml(WIRING)
    preview = _task(wiring_tasks, "Preview the rotation wiring changes in check mode")
    assert "ansible_check_mode" in preview["when"]
    assert preview["changed_when"] is True
    message = preview["ansible.builtin.debug"]["msg"]
    assert "dify_docker_rotation_change_names" in message
    # Names only: no draft value, no webhook id, no graph ever reaches the report.
    assert "json" not in message
    assert "value" not in message


def test_missing_agent_or_webhook_fails_with_its_name():
    agent_tasks = _yaml(AGENT)
    resolve_id_index = next(
        index
        for index, task in enumerate(agent_tasks)
        if task["name"] == "Resolve the agent app id by name"
    )
    named_asserts = [
        (index, task)
        for index, task in enumerate(agent_tasks)
        if "ansible.builtin.assert" in task
        and "dify_docker_rotation_agent_app_name" in task["ansible.builtin.assert"]["fail_msg"]
    ]
    # Duplicate app, missing app, missing webhook trigger, missing webhook id.
    assert len(named_asserts) == 4
    assert named_asserts[1][0] > resolve_id_index
    for _, task in named_asserts:
        assert task.get("no_log") is not True, task["name"]
        assert "{{ dify_docker_rotation_agent_app_name }}" in task["ansible.builtin.assert"]["fail_msg"]

    wiring_tasks = _yaml(WIRING)
    rotation_found = _task(wiring_tasks, "Assert the rotation app was found")
    assert "{{ dify_docker_rotation_app_name }}" in rotation_found["ansible.builtin.assert"]["fail_msg"]
    assert rotation_found.get("no_log") is not True


def test_webhook_and_draft_tasks_are_no_log():
    secret_safe_modules = ("ansible.builtin.uri", "ansible.builtin.set_fact")
    for path in (WIRING, AGENT):
        for task in _yaml(path):
            if any(module in task for module in secret_safe_modules):
                assert task.get("no_log") is True, task["name"]

    include = _task(_yaml(WIRING), "Wire each rotation agent's webhook trigger")
    assert include["ansible.builtin.include_tasks"] == "wire_rotation_agent.yml"
    assert include.get("no_log") is True
    assert include["loop"] == "{{ dify_docker_rotation_agent_keys }}"
    assert include["loop_control"]["loop_var"] == "dify_docker_rotation_agent_key"
