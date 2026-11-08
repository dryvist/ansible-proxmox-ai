"""The tier2 rotation wiring: derived agents, patch scope, check mode, secrets."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WIRING = ROOT / "roles/dify_docker/tasks/wire_rotation_webhooks.yml"
AGENT = ROOT / "roles/dify_docker/tasks/wire_rotation_agent.yml"
MAIN = ROOT / "roles/dify_docker/tasks/main.yml"
DEFAULTS = ROOT / "roles/dify_docker/defaults/main/20-agent-apps.yml"
KNOBS = ("ROTATION_SPEED", "ROTATION_OFFSET", "ROTATION_PAUSED")
GATED = ("ansible.builtin.uri",)
URL_ADD = "Add the agent trigger URL to the rotation patch when it differs"
ID_ADD = "Add the agent webhook id to the rotation patch when the stored digest differs"
DIGEST_ADD = "Add the agent webhook digest to the rotation patch when the stored digest differs"


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dump(path):
    return yaml.safe_dump(_yaml(path), width=1000)


def _task(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def _flat(text):
    """Folded scalars keep more-indented lines; compare whitespace-folded text."""
    return " ".join(text.split())


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
        "dify_docker_agent_dsl_select | length == 0"
        " or dify_docker_rotation_app_name in dify_docker_agent_dsl_select",
    ]


def test_agent_set_is_derived_from_the_rotation_draft_variables():
    wiring_tasks = _yaml(WIRING)
    id_keys = _task(wiring_tasks, "Collect the rotation draft webhook id keys")
    agent_keys = _task(wiring_tasks, "Derive the rotation agent keys from paired draft variables")
    trigger_expression = agent_keys["ansible.builtin.set_fact"]["dify_docker_rotation_agent_keys"]
    assert "select('match', '^[A-Z0-9_]+_WEBHOOK_ID$')" in str(id_keys["ansible.builtin.set_fact"])
    assert "select('match', '^[A-Z0-9_]+_TRIGGER_URL$')" in trigger_expression
    assert "select('in', dify_docker_rotation_webhook_id_keys)" in trigger_expression

    mapping = _task(_yaml(AGENT), "Map the rotation agent key to its Dify app name")
    assert mapping["ansible.builtin.set_fact"]["dify_docker_rotation_agent_app_name"].strip() == (
        "{{ dify_docker_rotation_agent_key | lower | regex_replace('_', '-') }}"
    )
    # No hard-coded agent list: the catalog example name never appears.
    assert "AI_NEWS" not in _dump(WIRING) and "AI_NEWS" not in _dump(AGENT)
    assert _yaml(DEFAULTS)["dify_docker_rotation_app_name"] == "tier2-rotation"


def test_patch_carries_only_the_per_agent_variables():
    resolve = _task(_yaml(AGENT), "Resolve the agent draft variables for this key")
    resolve_names = str(resolve["ansible.builtin.set_fact"])
    assert resolve_names.count("selectattr('name', 'equalto'") == 3
    for suffix in ("_TRIGGER_URL", "_WEBHOOK_ID", "_WEBHOOK_DIGEST"):
        assert f"dify_docker_rotation_agent_key ~ '{suffix}'" in resolve_names

    body = _task(_yaml(WIRING), "Sync the rotation draft environment variables")[
        "ansible.builtin.uri"
    ]["body"]
    assert body["graph"] == "{{ dify_docker_rotation_draft.json.graph }}"
    assert body["features"] == "{{ dify_docker_rotation_draft.json.features }}"
    assert body["hash"] == "{{ dify_docker_rotation_draft.json.hash }}"
    patch = body["environment_variable_patch"]
    assert patch["environment_variables"] == "{{ dify_docker_rotation_patch_variables }}"
    assert patch["deleted_environment_variable_ids"] == []


def test_each_patch_entry_is_added_by_its_own_condition():
    tasks = _yaml(AGENT)
    resolved_vars = (
        "dify_docker_rotation_agent_trigger_url_var[0]",
        "dify_docker_rotation_agent_webhook_id_var[0]",
        "dify_docker_rotation_agent_webhook_digest_var[0]",
    )
    write_tasks = [
        task
        for task in tasks
        if "dify_docker_rotation_patch_variables" in str(task.get("ansible.builtin.set_fact", {}))
    ]
    assert [task["name"] for task in write_tasks] == [URL_ADD, ID_ADD, DIGEST_ADD]
    for task in write_tasks:
        fact = task["ansible.builtin.set_fact"]
        patch_expression = _flat(fact["dify_docker_rotation_patch_variables"])
        assert patch_expression.count("combine({'value':") == 1, task["name"]
        assert sum(var in patch_expression for var in resolved_vars) == 1, task["name"]
        change_expression = _flat(fact["dify_docker_rotation_change_names"])
        assert change_expression.count("dify_docker_rotation_change_names | default([])") == 1


def test_webhook_id_currency_is_proven_by_the_digest_variable():
    agent_tasks = _yaml(AGENT)
    record = _task(agent_tasks, "Record the expected agent webhook id digest")
    digest_fact = record["ansible.builtin.set_fact"]["dify_docker_rotation_agent_webhook_id_digest"]
    assert "dify_docker_rotation_agent_webhook.json.webhook_id | hash('sha256')" in digest_fact

    detect = _task(agent_tasks, "Detect whether the agent draft variables need the wiring")
    digest_detection = _flat(
        detect["ansible.builtin.set_fact"]["dify_docker_rotation_agent_webhook_digest_matches"]
    )
    # The stored companion digest decides: missing or different rewrites, a
    # masked non-empty WEBHOOK_ID proves nothing, a match leaves both alone.
    assert (
        "dify_docker_rotation_agent_webhook_digest_var[0].value | default('', true)"
        " == dify_docker_rotation_agent_webhook_id_digest"
    ) in digest_detection
    assert "| length == 0" not in digest_detection
    for name in (ID_ADD, DIGEST_ADD):
        assert _task(agent_tasks, name)["when"] == (
            "not dify_docker_rotation_agent_webhook_digest_matches | bool"
        ), name
    id_expression = _flat(_task(agent_tasks, ID_ADD)["ansible.builtin.set_fact"]["dify_docker_rotation_patch_variables"])
    digest_expression = _flat(_task(agent_tasks, DIGEST_ADD)["ansible.builtin.set_fact"]["dify_docker_rotation_patch_variables"])
    assert "dify_docker_rotation_agent_webhook.json.webhook_id" in id_expression
    assert "dify_docker_rotation_agent_webhook_id_digest" in digest_expression


def test_webhook_digest_variable_is_created_with_a_deterministic_id():
    digest = _flat(
        _task(_yaml(AGENT), DIGEST_ADD)["ansible.builtin.set_fact"][
            "dify_docker_rotation_patch_variables"
        ]
    )
    # Reuse the existing variable's entry when present by name; otherwise build
    # one with a deterministic id, which the Dify patch appends because the id
    # is new and the names stay unique.
    assert "dify_docker_rotation_agent_webhook_digest_var[0] | default(" in digest
    assert (
        "{'id': (dify_docker_rotation_app_name ~ ':' ~ dify_docker_rotation_agent_key"
        " ~ '_WEBHOOK_DIGEST') | to_uuid,"
    ) in digest
    assert "'name': dify_docker_rotation_agent_key ~ '_WEBHOOK_DIGEST'," in digest
    assert "'value_type': 'string'}" in digest
    assert "| combine({'value': dify_docker_rotation_agent_webhook_id_digest})" in digest


def test_trigger_url_is_compared_exactly_and_a_trailing_slash_is_rewritten():
    tasks = _yaml(AGENT)
    detect = _task(tasks, "Detect whether the agent draft variables need the wiring")
    url_detection = _flat(
        detect["ansible.builtin.set_fact"]["dify_docker_rotation_agent_trigger_url_matches"]
    )
    # Exact string compare: only the expected value is slash-stripped, so a
    # stored trailing slash counts as different and is rewritten.
    assert (
        "dify_docker_rotation_agent_trigger_url_var[0].value | default('', true)"
        " == (dify_docker_trigger_url | regex_replace('/+$', ''))"
    ) in url_detection
    url_add = _task(tasks, URL_ADD)
    assert url_add["when"] == "not dify_docker_rotation_agent_trigger_url_matches | bool"
    assert (
        "dify_docker_rotation_agent_trigger_url_var[0]"
        " | combine({'value': dify_docker_trigger_url | regex_replace('/+$', '')})"
    ) in _flat(url_add["ansible.builtin.set_fact"]["dify_docker_rotation_patch_variables"])


def test_operator_knobs_are_never_wired():
    for path in (WIRING, AGENT):
        text = path.read_text(encoding="utf-8")
        for knob in KNOBS:
            assert knob.lower() not in text.lower(), (path.name, knob)


def test_wiring_is_idempotent_and_enables_triggers_only_when_disabled():
    wiring_tasks = _yaml(WIRING)
    agent_tasks = _yaml(AGENT)
    for name in (
        "Sync the rotation draft environment variables",
        "Publish the rotation workflow with the wired webhooks",
    ):
        when = _task(wiring_tasks, name)["when"]
        assert "not ansible_check_mode" in when
        assert "dify_docker_rotation_patch_variables | length > 0" in when

    detection = _flat(
        str(_task(agent_tasks, "Detect whether the agent draft variables need the wiring"))
    )
    assert "dify_docker_trigger_url" in detection
    assert "dify_docker_rotation_agent_webhook_id_digest" in detection

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
    read_only = ("GET", "HEAD", "OPTIONS")
    allowed = {
        "ansible.builtin.debug",
        "ansible.builtin.include_tasks",
        "ansible.builtin.set_fact",
        "ansible.builtin.assert",
        "ansible.builtin.uri",
    }
    for path in (WIRING, AGENT):
        for task in _yaml(path):
            uri = task.get("ansible.builtin.uri", {})
            if uri and uri.get("method") not in read_only:
                assert "not ansible_check_mode" in task.get("when", []), task["name"]
            # The wiring files only read and call the console API; no module
            # writes to the target.
            assert not any(
                key.startswith("ansible.") and key not in allowed for key in task
            ), task["name"]

    preview = _task(_yaml(WIRING), "Preview the rotation wiring changes in check mode")
    assert "ansible_check_mode" in preview["when"]
    assert preview["changed_when"] is True
    message = preview["ansible.builtin.debug"]["msg"]
    assert "dify_docker_rotation_change_names" in message
    # Names only: no draft value, no webhook id, no graph reaches the report.
    assert "json" not in message and "value" not in message


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
    for _, task in named_asserts[:-1]:
        assert task.get("no_log") is not True, task["name"]
    # The webhook-id assert keeps its name-bearing message but is censored: an
    # uncensored failure would evaluate the id into the task output.
    assert named_asserts[-1][1].get("no_log") is True
    for _, task in named_asserts:
        fail_msg = task["ansible.builtin.assert"]["fail_msg"]
        assert "{{ dify_docker_rotation_agent_app_name }}" in fail_msg

    rotation_found = _task(_yaml(WIRING), "Assert the rotation app was found")
    assert "{{ dify_docker_rotation_app_name }}" in rotation_found["ansible.builtin.assert"]["fail_msg"]
    assert rotation_found.get("no_log") is not True


def test_webhook_and_webhook_id_tasks_are_no_log():
    for path in (WIRING, AGENT):
        for task in _yaml(path):
            touches_webhook_id = (
                "webhook id" in task.get("name", "").lower()
                or "webhook_id" in yaml.safe_dump(task, width=1000)
            )
            if touches_webhook_id or "ansible.builtin.uri" in task or "ansible.builtin.set_fact" in task:
                assert task.get("no_log") is True, task["name"]

    include = _task(_yaml(WIRING), "Wire each rotation agent's webhook trigger")
    assert include["ansible.builtin.include_tasks"] == "wire_rotation_agent.yml"
    assert include.get("no_log") is True
    assert include["loop"] == "{{ dify_docker_rotation_agent_keys }}"
    assert include["loop_control"]["loop_var"] == "dify_docker_rotation_agent_key"
    # The webhook-id assertion evaluates the id into its failure output, so it
    # is censored like every other task that can hold the value.
    assert _task(_yaml(AGENT), "Assert the agent webhook id was returned").get("no_log") is True
