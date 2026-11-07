"""Private agent DSLs: an empty-by-default list, fetched only with the bearer token."""

import ast
import hashlib
import re
from pathlib import Path

from jinja2 import Environment

from _agent_dsl_shared import _task, _yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
AGENT_DSLS = ROOT / "roles/dify_docker/vars/agent_dsls.yml"
IMPORT_LOOP = ROOT / "roles/dify_docker/tasks/import_agent_dsls.yml"
IMPORT_ONE = ROOT / "roles/dify_docker/tasks/import_agent_dsl.yml"

FETCH_TASK = "Fetch the pinned agent DSL and verify its sha256 without redirects"
PRIVATE_LOOP_TASK = "Reconcile each private agent DSL"
TOKEN = "fixture-token"


def _render(expression, **context):
    # Plain Jinja with the two Ansible filters the expressions use.
    env = Environment()
    env.filters["bool"] = bool
    env.filters["combine"] = lambda base, extra: {**base, **extra}
    env.filters["regex_replace"] = lambda value, pattern, replacement="": re.sub(
        pattern, replacement, str(value)
    )
    env.tests["failed"] = lambda result: bool(result.get("failed"))
    return env.from_string(expression).render(context)


def _condition_matches(expression, **context):
    return _render("{{ " + expression + " }}", **context) == "True"


def test_private_list_and_token_are_empty_by_default():
    defaults = _yaml(DEFAULTS)

    assert defaults["dify_docker_private_agent_dsls"] == []
    assert defaults["dify_docker_private_agent_dsl_token"] == ""


def test_pinned_sources_file_does_not_override_supplied_private_values():
    # include_vars outranks inventory and role defaults, so defining either name
    # there would replace a value supplied at converge time.
    pins = _yaml(AGENT_DSLS)

    assert not [name for name in pins if "private" in name]
    assert "dify_docker_agent_dsls" not in pins


def test_private_sources_remain_a_separate_import_loop_after_catalog_entries():
    tasks = _yaml(IMPORT_LOOP)
    public = _task(tasks, "Reconcile each catalog agent DSL")
    private = _task(tasks, PRIVATE_LOOP_TASK)

    assert public["loop"] == "{{ dify_docker_agent_dsls }}"
    assert private["loop"] == (
        "{{ dify_docker_private_agent_dsls | map('combine', {'private': true}) | list }}"
    )
    assert tasks.index(public) < tasks.index(private)


def test_private_entries_are_marked_and_skipped_without_a_token():
    loop = _task(_yaml(IMPORT_LOOP), PRIVATE_LOOP_TASK)

    assert loop["ansible.builtin.include_tasks"] == "import_agent_dsl.yml"
    assert loop["loop_control"]["loop_var"] == "dify_docker_agent_dsl"
    assert loop["loop_control"]["label"] == "private agent DSL"
    assert "dify_docker_private_agent_dsl_token | length > 0" in loop["when"]
    assert "dify_docker_import_agent_dsls | bool" in loop["when"]
    assert "not ansible_check_mode" not in loop["when"]

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
    headers = fetch["ansible.builtin.uri"]["headers"]

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
    request = fetch["ansible.builtin.uri"]

    assert fetch["no_log"] is True
    assert request["method"] == "GET"
    assert request["status_code"] == 200
    assert request["follow_redirects"] == "none"
    assert request["return_content"] is True
    assert "dify_docker_dsl_fetch.content" in fetch["failed_when"]
    assert "hash('sha256')" in fetch["failed_when"]
    assert "dify_docker_agent_dsl.sha256" in fetch["failed_when"]

    write = _task(_yaml(IMPORT_ONE), "Write the verified private agent DSL for live import")
    assert write["ansible.builtin.copy"]["content"] == "{{ dify_docker_dsl_fetch.content }}"
    assert write["ansible.builtin.copy"]["mode"] == "0600"
    assert "not ansible_check_mode" in write["when"]
    assert write["no_log"] is True


def test_check_mode_previews_private_app_name_without_printing_private_content():
    importer = _yaml(IMPORT_ONE)
    resolve_name = _task(importer, "Resolve the private app preview name from its catalog entry")
    preview = next(task for task in importer if task.get("name") == "Preview the catalog app create or update")
    assert resolve_name["no_log"] is True

    catalog_entries = [
        ({"name": "catalog-agent-name", "url": "https://example.test/hidden.dsl.yml"}, "catalog-agent-name"),
        ({"url": "https://example.test/agent-file.dsl.yml"}, "agent-file"),
    ]
    for entry, expected_name in catalog_entries:
        preview_name = _render(
            resolve_name["ansible.builtin.set_fact"]["dify_docker_dsl_preview_name"],
            dify_docker_agent_dsl={**entry, "private": True},
        )
        assert preview_name == expected_name

    preview_name = _render(
        resolve_name["ansible.builtin.set_fact"]["dify_docker_dsl_preview_name"],
        dify_docker_agent_dsl={**catalog_entries[0][0], "private": True},
    )
    for matches, action in (([], "create"), ([{"name": "private-agent-name"}], "overwrite")):
        message = _render(
            preview["ansible.builtin.debug"]["msg"],
            dify_docker_dsl_app_matches=matches,
            dify_docker_agent_dsl={"private": True},
            dify_docker_dsl_name="dsl-document-internal-name",
            dify_docker_dsl_preview_name=preview_name,
        )
        assert message == f'Would {action} private Dify app "catalog-agent-name".'
        assert "dsl-document-internal-name" not in message
        assert "dify_docker_dsl_content" not in message
        assert "prompt" not in message.lower()
    assert preview.get("no_log") is not True

    # A nameless private entry must still render the import result, through the file-stem fallback.
    result = _task(importer, "Agent DSL import result")
    result_message = _render(
        result["ansible.builtin.debug"]["msg"],
        dify_docker_agent_dsl={"url": "https://example.test/agent-file.dsl.yml", "private": True},
        dify_docker_dsl_preview_name="agent-file",
        dify_docker_dsl_confirm={},
        dify_docker_dsl_import={},
    )
    assert result_message.startswith("agent DSL agent-file:")

    for task in importer:
        if task is preview:
            continue
        if any(value in str(task) for value in ("dify_docker_dsl_content", "dify_docker_dsl_document")):
            assert task.get("no_log") is True, task["name"]


def test_private_check_mode_reads_verified_dsl_in_memory_without_target_writes_or_deletes():
    importer = _yaml(IMPORT_ONE)
    read = _task(importer, "Read the catalog agent DSL into memory for check mode")
    verify = _task(importer, "Verify the catalog agent DSL sha256 in check mode")
    fetch = _task(importer, FETCH_TASK)
    write = _task(importer, "Write the verified private agent DSL for live import")
    slurp = _task(importer, "Read the verified agent DSL")
    select_source = _task(importer, "Select the verified in-memory or file-backed agent DSL")
    cleanup = _task(importer, "Remove the fetched agent DSL copy")

    assert read["ansible.builtin.uri"]["url"] == "{{ dify_docker_agent_dsl.url }}"
    assert read["ansible.builtin.uri"]["method"] == "GET"
    assert read["ansible.builtin.uri"]["return_content"] is True
    assert read["ansible.builtin.uri"]["status_code"] == 200
    assert read["ansible.builtin.uri"]["follow_redirects"] == "none"
    assert read["check_mode"] is False
    assert read["changed_when"] is False
    assert read["no_log"] is True
    assert "ansible_check_mode" in read["when"]

    private_headers = ast.literal_eval(
        _render(
            read["ansible.builtin.uri"]["headers"],
            dify_docker_agent_dsl={"private": True},
            dify_docker_private_agent_dsl_token=TOKEN,
        )
    )
    public_headers = ast.literal_eval(
        _render(
            read["ansible.builtin.uri"]["headers"],
            dify_docker_agent_dsl={},
            dify_docker_private_agent_dsl_token=TOKEN,
        )
    )
    assert private_headers == {"Authorization": f"Bearer {TOKEN}"}
    assert public_headers == {}

    checksum_rule = verify["ansible.builtin.assert"]["that"][1]
    assert "content | hash('sha256')" in checksum_rule
    assert "dify_docker_agent_dsl.sha256" in checksum_rule
    assert verify["no_log"] is True
    assert verify["ansible.builtin.assert"]["fail_msg"] == (
        "The catalog agent DSL did not match its pinned sha256."
    )
    assert select_source["no_log"] is True
    assert "content | b64encode" in str(
        select_source["ansible.builtin.set_fact"]["dify_docker_dsl_raw"]
    )

    payload = "app:\n  name: example-private-agent\n"
    checksum = hashlib.sha256(payload.encode()).hexdigest()
    hash_env = Environment()
    hash_env.filters["hash"] = lambda value, algorithm: hashlib.new(
        algorithm, value.encode()
    ).hexdigest()
    checksum_check = hash_env.from_string("{{ " + checksum_rule + " }}")
    context = {
        "dify_docker_dsl_check_read": {"content": payload},
        "dify_docker_agent_dsl": {"sha256": checksum},
    }
    assert checksum_check.render(context) == "True"
    context["dify_docker_agent_dsl"]["sha256"] = "0" * 64
    assert checksum_check.render(context) == "False"

    # Keep the check-mode proof fail-closed if another target-mutating module
    # is added to this importer later.
    target_mutating_modules = (
        "ansible.builtin.get_url",
        "ansible.builtin.file",
        "ansible.builtin.copy",
        "ansible.builtin.template",
        "ansible.builtin.lineinfile",
        "ansible.builtin.blockinfile",
        "ansible.builtin.replace",
        "ansible.builtin.assemble",
        "ansible.builtin.unarchive",
        "ansible.builtin.synchronize",
        "ansible.builtin.command",
        "ansible.builtin.shell",
    )
    target_mutations = [
        task
        for task in importer
        if any(module in task for module in target_mutating_modules)
        or (
            "ansible.builtin.uri" in task
            and "dest" in task["ansible.builtin.uri"]
        )
    ]
    assert target_mutations == [write, cleanup]
    assert "not ansible_check_mode" in write["when"]
    assert "not ansible_check_mode" in cleanup["when"]
    assert slurp["ansible.builtin.slurp"]["src"].startswith("/tmp/dify-agent-dsl-")
    assert "not ansible_check_mode" in slurp["when"]

    context = {
        "ansible_check_mode": True,
        "dify_docker_agent_dsl": {"private": True},
    }
    assert _condition_matches(fetch["when"][0], **context)
    assert not _condition_matches(fetch["when"][1], **context)
    assert all(not _condition_matches(condition, **context) for condition in cleanup["when"])
    assert not _condition_matches(slurp["when"][0], **context)
    for task in importer:
        if "dify_docker_dsl_check_read" in str(task):
            assert task.get("no_log") is True, task["name"]
