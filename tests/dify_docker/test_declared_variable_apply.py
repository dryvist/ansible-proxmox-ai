"""Declared catalog app variables: draft writes, secret id minting, publish order, prune and KV reads."""

import json

import yaml

from _agent_dsl_shared import IMPORT_LOOP, IMPORT_ONE, _expanded_yaml
from _declared_variables_shared import (
    APPLY,
    DECLARED_PUBLISH,
    EDITED,
    ONE,
    PRE,
    PRUNE,
    PRUNE_INCLUDE,
    RECONCILE,
    RECORD,
    RESOLVE,
    SELECT_STALE,
    SENSITIVE,
    WRITE,
    _env,
    _fact,
    _render,
    _task,
    _yaml,
)
from _defaults import load_defaults


def test_declared_variables_are_written_to_the_draft_with_secret_types_and_named_patch_only():
    apply_tasks = _yaml(APPLY)
    expression = _fact(apply_tasks, "Add each declared variable to the draft environment variable patch",
                       "dify_docker_dsl_env_patch")
    draft = {"json": {"environment_variables": [{"id": "env-1", "name": "EXAMPLE_TOKEN"},
                                                 {"id": "env-2", "name": "OTHER"}]}}
    patch = _render(expression, dify_docker_dsl_env_patch=[], dify_docker_dsl_draft=draft,
                    dify_docker_dsl_name="example-app",
                    item={"name": "EXAMPLE_TOKEN", "secret": True, "value": "x"})
    assert patch == [{"id": "env-1", "name": "EXAMPLE_TOKEN", "value": "x", "value_type": "secret"}]
    new_plain = _render(expression, dify_docker_dsl_env_patch=[], dify_docker_dsl_draft=draft,
                        dify_docker_dsl_name="example-app",
                        item={"name": "NEW_VAR", "secret": False, "value": "y"})
    assert new_plain == [{"id": "uuid:example-app:NEW_VAR", "name": "NEW_VAR", "value": "y",
                          "value_type": "string"}]
    write = _task(apply_tasks, "Write the declared variables to the draft environment variables")
    assert write["ansible.builtin.uri"]["body"]["environment_variable_patch"] == {
        "environment_variables": "{{ dify_docker_dsl_env_patch }}",
        "deleted_environment_variable_ids": [],
    }


def test_secret_ids_mint_with_the_ttl_and_keep_only_the_newest_older_id():
    assert load_defaults()["dify_docker_agent_secret_id_ttl"] == "720h"
    apply_tasks = _yaml(APPLY)
    mint = _task(apply_tasks, "Mint an AppRole secret id for each declared secret id variable")
    assert mint["ansible.builtin.uri"]["method"] == "POST"
    assert mint["ansible.builtin.uri"]["url"].endswith("/secret-id")
    body = mint["ansible.builtin.uri"]["body"]
    assert body["ttl"] == "{{ dify_docker_agent_secret_id_ttl }}"
    metadata = _env().from_string(body["metadata"]).render(
        dify_docker_dsl_name="example-app", dify_docker_agent_secret_id_app_metadata_key="dify_app")
    assert json.loads(metadata) == {"dify_app": "example-app"}

    prune = _yaml(PRUNE)
    ages = [
        {"accessor": "a1", "created": "2026-11-01T00:00:00Z", "app": "example-app"},
        {"accessor": "a2", "created": "2026-11-02T00:00:00Z", "app": "example-app"},
        {"accessor": "a3", "created": "2026-11-03T00:00:00Z", "app": "example-app"},
        {"accessor": "a4", "created": "2026-11-04T00:00:00Z", "app": "example-app"},
    ]
    stale = _render(_fact(prune, SELECT_STALE, "dify_docker_dsl_secret_id_stale"),
                    dify_docker_dsl_name="example-app", dify_docker_dsl_secret_id_ages=ages)
    assert sorted(stale) == ["a1", "a2", "a3"]
    collect = _fact(prune, "Collect each accessor with its creation time to the second and the app its metadata names",
                    "dify_docker_dsl_secret_id_ages")
    item = {"item": "acc-1", "json": {"data": {"creation_time": "2026-11-10T19:30:01.123456789Z",
                                               "metadata": {"dify_app": "example-app"}}}}
    assert _render(collect, dify_docker_dsl_secret_id_ages=[],
                   dify_docker_agent_secret_id_app_metadata_key="dify_app", item=item
                   ) == [{"accessor": "acc-1", "created": "2026-11-10T19:30:01Z", "app": "example-app"}]
    lookups = _task(prune, "Look up the creation time of each secret id accessor")["loop"]
    assert _render(lookups, dify_docker_dsl_secret_id_list={"json": {"data": {"keys": ["a", "b"]}}}) == ["a", "b"]
    assert _render(lookups, dify_docker_dsl_secret_id_list={"json": {"errors": []}}) == []


def test_check_mode_resolves_reads_only_and_never_mints_or_writes():
    for path in (PRE, ONE):
        for task in _yaml(path):
            uri = task.get("ansible.builtin.uri")
            if uri:
                assert uri.get("method") == "GET", f"{path.name}: {task.get('name')}"
    reconcile = _yaml(RECONCILE)
    for name in (WRITE, PRUNE_INCLUDE, DECLARED_PUBLISH):
        assert "not ansible_check_mode" in _task(reconcile, name)["when"], name
    report = _task(_yaml(PRE), "Report the declared variables a live run would set, by name only")
    assert "ansible_check_mode" in report["when"]


def test_publish_runs_after_the_write_and_prune_runs_after_the_publish():
    names = [task.get("name") for task in _expanded_yaml(IMPORT_ONE)]
    import_index = names.index("Import the catalog DSL when reconciliation requires it")
    publish_index = names.index("Publish the changed catalog workflow for API callers")
    assert names.index(RESOLVE) < import_index < names.index(WRITE) < publish_index
    assert names.index(WRITE) < names.index(DECLARED_PUBLISH) < names.index(PRUNE_INCLUDE)
    assert publish_index < names.index(PRUNE_INCLUDE)


def test_every_task_that_holds_a_resolved_value_is_no_log():
    for path in EDITED:
        for task in _yaml(path):
            text = yaml.safe_dump(task)
            if any(name in text for name in SENSITIVE):
                assert task.get("no_log") is True, f"{path.name}: {task.get('name')}"


def test_null_missing_or_empty_kv_field_is_unresolved_and_never_the_string_none():
    keep = _fact(_yaml(ONE), "Keep the declared KV field value", "dify_docker_dsl_variable_values")
    unresolved = _fact(_yaml(PRE), "List the declared variables that have no value yet",
                       "dify_docker_dsl_unresolved_names")
    entry = {"name": "NULLED", "secret": True, "kv": {"path": "example/app", "field": "token"}}
    for secret in ({"token": None}, {}, {"token": ""}):
        values = _render(keep, dify_docker_dsl_variable_values=[], dify_docker_dsl_kv_read={"secret": secret},
                         dify_docker_dsl_variable_entry=entry)
        assert values == [{"name": "NULLED", "secret": True, "value": ""}], secret
        assert _render(unresolved, dify_docker_dsl_resolve_names=["NULLED"],
                       dify_docker_dsl_variable_values=values) == ["NULLED"], secret
    present = _render(keep, dify_docker_dsl_variable_values=[], dify_docker_dsl_kv_read={"secret": {"token": "v"}},
                      dify_docker_dsl_variable_entry=entry)
    assert present == [{"name": "NULLED", "secret": True, "value": "v"}]


def test_prune_destroys_only_older_accessors_and_never_one_minted_in_the_run():
    select = _fact(_yaml(PRUNE), SELECT_STALE, "dify_docker_dsl_secret_id_stale")
    older = [
        {"accessor": "old-1", "created": "2026-11-01T00:00:00Z", "app": "example-app"},
        {"accessor": "old-2", "created": "2026-11-02T00:00:00Z", "app": "example-app"},
        {"accessor": "legacy", "created": "2026-10-01T00:00:00Z", "app": ""},
    ]
    for minted_count in (1, 2, 3):
        minted = [
            {"accessor": f"new-{number}", "created": f"2026-11-10T00:00:0{number}Z", "app": "example-app"}
            for number in range(1, minted_count + 1)
        ]
        stale = _render(select, dify_docker_dsl_name="example-app", dify_docker_dsl_secret_id_ages=older + minted,
                        dify_docker_dsl_minted_accessors=[entry["accessor"] for entry in minted])
        assert stale == ["old-1"], minted_count
    assert _render(select, dify_docker_dsl_name="example-app", dify_docker_dsl_secret_id_ages=older[:2]) == ["old-1"]
    assert _render(select, dify_docker_dsl_name="example-app", dify_docker_dsl_secret_id_ages=older[:1]) == []


def test_minted_accessors_accumulate_across_apps_without_secret_ids():
    record = _fact(_yaml(APPLY), "Record each minted secret id accessor for this run's prune protection",
                   "dify_docker_dsl_minted_accessors")
    result = _render(record, dify_docker_dsl_minted_accessors=["new-1"],
                     item={"json": {"data": {"secret_id_accessor": "new-2", "secret_id": "SECRET-VALUE"}}})
    assert result == ["new-1", "new-2"]
    assert "SECRET-VALUE" not in str(result)
    assert _render(record, item={"json": {"data": {"secret_id_accessor": "new-1"}}}) == ["new-1"]


def test_minted_accessor_list_is_reset_once_per_run_and_kept_across_app_iterations():
    loop = _yaml(IMPORT_LOOP)
    names = [task.get("name") for task in loop]
    reset = names.index("Reset the secret id accessors minted in this run")
    assert reset < names.index("Reconcile each catalog agent DSL")
    assert loop[reset]["no_log"] is True
    clear = _task(_yaml(IMPORT_ONE), "Clear the declared app variable values from facts")
    assert "dify_docker_dsl_minted_accessors" not in clear["ansible.builtin.set_fact"]


def _replay_runs(runs, server):
    """Replay the real record and prune expressions over successive converges of one AppRole.

    runs holds one list per converge; each entry is an (app, mints) pair in reconcile order. The
    minted list resets once per converge, as the play-wide reset does. server maps each accessor
    to its (app, created) pair, with '' as the app for an accessor that carries no app metadata.
    Returns the destroyed accessors in order and the server's accessor set after each converge."""
    record = _fact(_yaml(APPLY), RECORD, "dify_docker_dsl_minted_accessors")
    select = _fact(_yaml(PRUNE), SELECT_STALE, "dify_docker_dsl_secret_id_stale")
    destroyed, after, serial = [], [], 0
    for run in runs:
        minted = []
        for app, mints in run:
            for _ in range(mints):
                serial += 1
                accessor = f"new-{serial}"
                server[accessor] = (app, f"2026-11-10T00:00:{serial:02d}Z")
                minted = _render(record, dify_docker_dsl_minted_accessors=minted,
                                 item={"json": {"data": {"secret_id_accessor": accessor}}})
            ages = [{"accessor": name, "created": created, "app": owner}
                    for name, (owner, created) in server.items()]
            for accessor in _render(select, dify_docker_dsl_name=app, dify_docker_dsl_secret_id_ages=ages,
                                    dify_docker_dsl_minted_accessors=minted):
                del server[accessor]
                destroyed.append(accessor)
            assert set(minted) <= set(server), "an accessor minted in this run was destroyed"
        after.append(set(server))
    return destroyed, after


def test_two_apps_sharing_one_approle_each_minting_one_id_keep_every_minted_accessor():
    destroyed, after = _replay_runs([[("example-app", 1), ("other-app", 1)]], {
        "ex-old-1": ("example-app", "2026-11-01T00:00:00Z"),
        "ex-old-2": ("example-app", "2026-11-02T00:00:00Z"),
        "ot-old-1": ("other-app", "2026-11-01T00:00:00Z"),
    })
    assert destroyed == ["ex-old-1"]
    assert after[-1] == {"ex-old-2", "ot-old-1", "new-1", "new-2"}


def test_one_app_with_three_secret_id_variables_on_one_approle_keeps_every_minted_accessor():
    destroyed, after = _replay_runs([[("example-app", 3)]], {
        "ex-old-1": ("example-app", "2026-11-01T00:00:00Z"),
        "ex-old-2": ("example-app", "2026-11-02T00:00:00Z"),
        "ot-old-1": ("other-app", "2026-11-01T00:00:00Z"),
    })
    assert destroyed == ["ex-old-1"]
    assert after[-1] == {"ex-old-2", "ot-old-1", "new-1", "new-2", "new-3"}


def test_an_unselected_apps_accessors_survive_three_runs_in_a_row():
    server = {
        "ex-old": ("example-app", "2026-11-01T00:00:00Z"),
        "ot-old": ("other-app", "2026-11-01T00:00:00Z"),
    }
    destroyed, after = _replay_runs([[("example-app", 1)]] * 3, server)
    assert all("ot-old" in state for state in after)
    assert "ot-old" not in destroyed
    assert "ex-old" not in after[-1]


def test_accessors_without_the_app_metadata_are_never_destroyed_and_are_counted():
    server = {
        "legacy-1": ("", "2026-11-01T00:00:00Z"),
        "legacy-2": ("", "2026-11-02T00:00:00Z"),
        "ex-old": ("example-app", "2026-11-03T00:00:00Z"),
    }
    destroyed, after = _replay_runs([[("example-app", 1)]] * 2, server)
    assert {"legacy-1", "legacy-2"} <= after[-1]
    assert "legacy-1" not in destroyed and "legacy-2" not in destroyed
    count = _fact(_yaml(PRUNE), "Count the accessors of this AppRole that carry no app metadata",
                  "dify_docker_dsl_untagged_accessor_count")
    ages = [{"accessor": name, "created": created, "app": owner} for name, (owner, created) in server.items()]
    assert _render(count, dify_docker_dsl_secret_id_ages=ages) == 2


def test_the_app_is_read_from_the_configured_metadata_key():
    collect = _fact(_yaml(PRUNE),
                    "Collect each accessor with its creation time to the second and the app its metadata names",
                    "dify_docker_dsl_secret_id_ages")
    item = {"item": "acc-1", "json": {"data": {"creation_time": "2026-11-10T19:30:01Z",
                                               "metadata": {"owner": "example-app"}}}}
    assert _render(collect, dify_docker_dsl_secret_id_ages=[], dify_docker_agent_secret_id_app_metadata_key="owner",
                   item=item)[0]["app"] == "example-app"
    assert _render(collect, dify_docker_dsl_secret_id_ages=[], dify_docker_agent_secret_id_app_metadata_key="dify_app",
                   item=item)[0]["app"] == ""
