from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
PLAYBOOK = ROOT / "playbooks/benchmark-run.yml"
TASKS = ROOT / "playbooks/tasks/benchmark-run"


def load_tasks(name: str) -> list[dict[str, object]]:
    return yaml.safe_load((TASKS / name).read_text())


def test_tau2_suite_prepares_clone_and_keeps_key_off_argv() -> None:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    assert plays[1]["vars"]["_benchmark_run_tau2_bench_url"] == (
        "https://github.com/JacobPEvans-ai/tau2-bench.git"
    )
    preflight = load_tasks("preflight.yml")
    suites = preflight[0]["ansible.builtin.assert"]["that"]
    assert any("tau2-banking-knowledge" in assertion for assertion in suites)
    preflight_include = next(
        task for task in preflight if task.get("ansible.builtin.import_tasks") == "preflight-tau2.yml"
    )
    assert preflight_include["when"] == "suite == 'tau2-banking-knowledge'"
    request = next(task for task in preflight if task.get("ansible.builtin.add_host"))
    tau2_request = request["ansible.builtin.add_host"]["benchmark_run_request"]
    assert tau2_request["api_key_kv_path"] == "{{ api_key_kv_path | default('') }}"
    assert tau2_request["api_key_kv_field"] == "{{ api_key_kv_field | default('') }}"
    assert tau2_request["tau2_domain"] == "{{ tau2_domain | default('banking_knowledge') }}"
    assert tau2_request["tau2_num_trials"] == "{{ tau2_num_trials | default(1) | int }}"
    assert tau2_request["tau2_max_concurrency"] == (
        "{{ tau2_max_concurrency | default(concurrency | default(1)) | int }}"
    )
    assert tau2_request["tau2_bench_commit"] == "{{ _benchmark_run_tau2_bench_commit }}"
    preflight_tau2 = load_tasks("preflight-tau2.yml")
    key_assert = next(
        task
        for task in preflight_tau2
        if task.get("when") == "suite == 'tau2-banking-knowledge'"
        and "api_key_kv_path" in str(task.get("ansible.builtin.assert", {}).get("that", []))
    )
    assert "api_key_kv_field" in str(key_assert["ansible.builtin.assert"]["that"])

    prepare = load_tasks("prepare.yml")
    prepare_include = next(
        task for task in prepare if task.get("ansible.builtin.import_tasks") == "prepare-tau2.yml"
    )
    assert prepare_include["when"] == "benchmark_run_request.suite == 'tau2-banking-knowledge'"
    prepare_tau2 = load_tasks("prepare-tau2.yml")
    clone_tasks = [task for task in prepare_tau2 if "tau2-bench" in str(task)]
    clone = next(task for task in clone_tasks if "clone" in task.get("ansible.builtin.command", {}).get("argv", []))
    assert "{{ _benchmark_run_tau2_bench_url }}" in clone["ansible.builtin.command"]["argv"]
    checkout = next(task for task in clone_tasks if "checkout" in task.get("ansible.builtin.command", {}).get("argv", []))
    assert "{{ benchmark_run_request.tau2_bench_commit }}" in checkout["ansible.builtin.command"]["argv"]
    head = next(task for task in clone_tasks if task.get("register") == "_benchmark_run_tau2_head")
    assert "rev-parse" in head["ansible.builtin.command"]["argv"]
    head_assert = next(
        task
        for task in prepare_tau2
        if task.get("name") == "Require the pinned tau2-bench commit at the clone head"
    )
    assert "_benchmark_run_tau2_head.stdout | trim ==" in str(head_assert["ansible.builtin.assert"]["that"])
    sync = next(task for task in clone_tasks if "sync" in task.get("ansible.builtin.command", {}).get("argv", []))
    assert sync["ansible.builtin.command"]["argv"][4] == "/usr/local/bin/uv"
    for task in clone_tasks:
        assert task.get("when") == "benchmark_run_request.suite == 'tau2-banking-knowledge'" or (
            task.get("when")
            and "tau2-banking-knowledge" in str(task.get("when"))
        )
    prepare_metadata = load_tasks("prepare-metadata.yml")
    run_metadata = next(task for task in prepare_metadata if task.get("name") == "Write client run metadata")
    assert run_metadata["ansible.builtin.copy"]["content"] is not None
    assert "tau2_bench_commit" in str(run_metadata)

    execute = load_tasks("execute.yml")
    dispatch = execute[0]["block"]
    tau2_dispatch = next(task for task in dispatch if task.get("ansible.builtin.import_tasks") == "tau2.yml")
    assert tau2_dispatch["when"] == "benchmark_run_request.suite == 'tau2-banking-knowledge'"

    tau2 = load_tasks("tau2.yml")
    vault_read = next(task for task in tau2 if task.get("community.hashi_vault.vault_kv2_get"))
    vault_read_task = vault_read["community.hashi_vault.vault_kv2_get"]
    assert vault_read_task["auth_method"] == "token"
    assert vault_read_task["token"] == "{{ openbao_secrets_converge_writer_token }}"
    assert vault_read_task["engine_mount_point"] == "{{ openbao_secrets_kv_mount }}"
    assert vault_read_task["path"] == "{{ benchmark_run_request.api_key_kv_path }}"
    assert vault_read["delegate_to"] == "localhost"
    assert vault_read["connection"] == "local"
    assert vault_read["become"] is False
    assert vault_read["check_mode"] is False
    assert vault_read["no_log"] is True
    key_assert = next(task for task in tau2 if task.get("ansible.builtin.assert"))
    assert "benchmark_run_request.api_key_kv_path" in key_assert["ansible.builtin.assert"]["fail_msg"]
    assert "benchmark_run_request.api_key_kv_field" in key_assert["ansible.builtin.assert"]["fail_msg"]
    assert key_assert["no_log"] is True

    run_task = next(task for task in tau2 if task.get("register") == "_benchmark_run_suite")
    run_argv = run_task["ansible.builtin.command"]["argv"]
    assert run_argv[:4] == ["pct", "exec", "{{ benchmark_run_request.client_vmid }}", "--"]
    env_index = run_argv.index("/usr/bin/env")
    for provider_default in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "ANTHROPIC_API_KEY"):
        assert run_argv[env_index : env_index + 10].count(provider_default) == 1
        assert run_argv[run_argv.index(provider_default) - 1] == "-u"
    assert "/bin/bash" in run_argv and "-c" in run_argv
    run_script = run_argv[run_argv.index("-c") + 1]
    for expected in (
        "IFS= read -r BENCH_ROUTER_KEY",
        "export BENCH_ROUTER_KEY",
        "exec uv run tau2 run",
        "--domain {{ benchmark_run_request.tau2_domain | quote }}",
        "{% if benchmark_run_request.tau2_domain == 'banking_knowledge' %}--retrieval-config bm25{% endif %}",
        "--num-trials {{ benchmark_run_request.tau2_num_trials | int }}",
        "--max-concurrency {{ benchmark_run_request.tau2_max_concurrency | quote }}",
        "--save-to {{ (benchmark_run_request.run_id ~ '-' ~ benchmark_run_request.tau2_domain) | quote }}",
    ):
        assert expected in run_script, expected
    assert "OPENAI_API_KEY" not in run_script
    assert run_task["vars"]["_benchmark_run_tau2_model"] == "{{ 'openai/' ~ benchmark_run_request.model }}"
    role_args = run_task["vars"]["_benchmark_run_tau2_role_args"]
    for expected in (
        "'api_base': benchmark_run_request.engine_url",
        "'api_key': 'os.environ/BENCH_ROUTER_KEY'",
        "'temperature': 0.0",
    ):
        assert expected in role_args
    for role in ("agent", "user", "nl-assertions", "env-interface"):
        assert f"--{role}-llm {{{{ _benchmark_run_tau2_model | quote }}}}" in run_script
        assert f"--{role}-llm-args {{{{ _benchmark_run_tau2_role_args | quote }}}}" in run_script
    assert run_task["ansible.builtin.command"]["stdin"] == (
        "{{ _benchmark_run_tau2_key_read.data.data[benchmark_run_request.api_key_kv_field] }}"
    )
    assert run_task["no_log"] is True
    find_task = next(task for task in tau2 if task.get("register") == "_benchmark_run_tau2_results")
    assert "{{ benchmark_run_request.run_id }}-{{ benchmark_run_request.tau2_domain }}/results.json" in str(find_task)
    for task in tau2:
        if task.get("register") == "_benchmark_run_suite":
            continue
        assert "OPENAI_API_KEY" not in str(task.get("ansible.builtin.command", {}).get("argv", []))
    copy_task = next(task for task in tau2 if task.get("name") == "Copy the tau2 results file into the run result directory")
    result_name = "{{ benchmark_run_request.suite }}-{{ benchmark_run_request.tau2_domain }}.json"
    assert result_name in copy_task["ansible.builtin.command"]["argv"][-1]

    always = load_tasks("execute.yml")[0]["always"]
    tau2_collect = next(task for task in always if task.get("name") == "Collect the tau2 results file from the client")
    assert tau2_collect["ansible.builtin.command"]["argv"][:2] == ["pct", "pull"]
    assert tau2_collect["ansible.builtin.command"]["argv"][-2].endswith(result_name)
    assert tau2_collect["when"] == [
        "benchmark_run_request.suite == 'tau2-banking-knowledge'",
        "_benchmark_run_suite.rc | default(1) == 0",
    ]
    tau2_summary = next(task for task in always if task.get("name") == "Read a safe tau2 summary from its results JSON")
    tau2_summary_argv = tau2_summary["ansible.builtin.command"]["argv"]
    assert tau2_summary_argv[0] == "jq"
    assert "average_reward" in tau2_summary_argv[-2]
    assert "simulations" in tau2_summary_argv[-2]
    assert "domain=" in tau2_summary_argv[-2]
    assert tau2_summary_argv[-3] == "{{ benchmark_run_request.tau2_domain }}"
