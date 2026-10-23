from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
PLAYBOOK = ROOT / "playbooks/benchmark-run.yml"
TASKS = ROOT / "playbooks/tasks/benchmark-run"
SERVING_DEFAULTS = ROOT / "roles/vllm_serving/defaults/main/00-engine.yml"


def load_tasks(name: str) -> list[dict[str, object]]:
    return yaml.safe_load((TASKS / name).read_text())


def test_runner_pins_tools_and_defaults_publishing_off() -> None:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    vars_ = plays[1]["vars"]
    assert vars_["_benchmark_run_evalscope_version"] == "1.13.0"
    assert vars_["_benchmark_run_promptfoo_version"] == "0.124.1"
    assert vars_["_benchmark_run_lmcache_version"] == "0.5.5"
    assert vars_["_benchmark_run_vllm_client_version"] == "{{ vllm_serving_version }}"
    assert vars_["_benchmark_run_vllm_client_image"] == (
        "vllm/vllm-openai-cpu:v{{ vllm_serving_version }}-x86_64"
    )
    serving = yaml.safe_load(SERVING_DEFAULTS.read_text())
    assert "../roles/vllm_serving/defaults/main/00-engine.yml" in plays[1]["vars_files"]
    assert "vllm_serving_version" in serving
    playbook_text = PLAYBOOK.read_text()
    assert "renovate: datasource=github-releases depName=LMCache/LMCache" in playbook_text

    preflight = load_tasks("preflight.yml")
    request_assert = preflight[0]["ansible.builtin.assert"]["that"]
    assert "not (publish | default(false) | bool)" in request_assert
    assert any("reasoning_arm" in assertion for assertion in request_assert)
    reasoning_assert = next(assertion for assertion in request_assert if "reasoning_arm" in assertion)
    assert "'high'" in reasoning_assert
    assert any(
        "item_limit" in assertion
        for task in preflight
        for assertion in task.get("ansible.builtin.assert", {}).get("that", [])
    )
    assert any(
        "evalscope_timeout_seconds" in assertion
        for task in preflight
        for assertion in task.get("ansible.builtin.assert", {}).get("that", [])
    )
    assert "default(3600)" in str(preflight)


def test_runner_has_pinned_bigcodebench_and_promptfoo_paths() -> None:
    preflight = load_tasks("preflight.yml")
    suites = preflight[0]["ansible.builtin.assert"]["that"]
    assert any("bigcodebench-hard" in assertion for assertion in suites)
    assert any("promptfoo-" in assertion for assertion in suites)

    tasks = load_tasks("bigcodebench.yml")
    evalscope_task = next(task for task in tasks if "Generate the pinned BigCodeBench" in task["name"])
    bigcodebench = evalscope_task["ansible.builtin.command"]["argv"]
    assert "/usr/local/bin/uv" in bigcodebench
    generation = bigcodebench[bigcodebench.index("--generation-config") + 1]
    sandbox = bigcodebench[bigcodebench.index("--sandbox") + 1]
    assert "chat_template_kwargs" in generation
    assert "enable_thinking" in generation
    assert "reasoning_effort" in generation
    assert "stream" in generation
    assert "timeout" in generation
    assert "item_limit" in str(bigcodebench)
    assert "pool_size" in sandbox
    assert "entrypoint" not in sandbox
    assert "infinity" not in sandbox
    assert evalscope_task["no_log"] is True
    task_names = [task["name"] for task in tasks]
    negative_control = task_names.index("Score the negative controls with the official evaluator")
    positive_control = task_names.index("Score canonical controls with the official ground-truth check")
    generation_index = task_names.index("Generate the pinned BigCodeBench-Hard sample with EvalScope")
    official_score = task_names.index("Score model samples with the official BigCodeBench evaluator")
    assert negative_control < positive_control < generation_index < official_score
    official_argv = tasks[official_score]["ansible.builtin.command"]["argv"]
    assert "--execution" in official_argv and "local" in official_argv
    assert "--selective_evaluate" in official_argv
    assert any("--check_gt_only" in task.get("ansible.builtin.command", {}).get("argv", []) for task in tasks)
    assert load_tasks("promptfoo.yml")[0]["no_log"] is True
    assert load_tasks("cache-run-with-metrics.yml")[0]["no_log"] is True

    promptfoo = load_tasks("promptfoo.yml")[0]["ansible.builtin.command"]["argv"]
    npx_index = promptfoo.index("/usr/local/bin/npx")
    assert promptfoo[npx_index - 2 : npx_index] == [
        "/usr/bin/env",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    ]
    assert "--max-concurrency" in promptfoo
    assert "--no-cache" in promptfoo
    assert any("BENCHMARK_CHAT_TEMPLATE_KWARGS" in argument for argument in promptfoo)

    prepare = load_tasks("prepare.yml")
    uv_check = next(task for task in prepare if task.get("register") == "_benchmark_run_uv_version")
    node_check = next(task for task in prepare if task.get("register") == "_benchmark_run_node_version")
    assert uv_check["ansible.builtin.command"]["argv"][-2] == "/usr/local/bin/uv"
    assert node_check["ansible.builtin.command"]["argv"][-2] == "/usr/local/bin/node"
    engine_probe = next(task for task in prepare if task.get("register") == "_benchmark_run_engine_probe")
    assert engine_probe["ansible.builtin.command"]["argv"][4] == "/usr/bin/curl"
    assert engine_probe["when"] == "benchmark_run_request.suite != 'promptfoo-echo-smoke'"
    engine_metrics = next(task for task in prepare if task.get("register") == "_benchmark_run_engine_start_time")
    assert "/usr/bin/curl" in engine_metrics["ansible.builtin.shell"]["cmd"]


def test_runner_checks_tagged_client_and_collects_raw_artifacts() -> None:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    assert plays[2]["hosts"] == "{{ node | default('localhost') }}"
    assert plays[2]["vars"]["benchmark_run_request"] == "{{ hostvars[node].benchmark_run_request }}"
    run_tasks = plays[2]["tasks"][0]
    assert "delegate_to" not in run_tasks

    preflight = load_tasks("preflight.yml")
    selected_node = next(task for task in preflight if task["name"] == "Add the selected Proxmox node to this run's inventory")
    assert selected_node["ansible.builtin.add_host"]["name"] == "{{ node }}"

    prepare = load_tasks("prepare.yml")
    assert any("benchmark-client" in task.get("name", "") for task in prepare)
    assert any("engine" in task.get("name", "").lower() for task in prepare)
    running_assert = next(task for task in prepare if "_benchmark_run_client_status.stdout" in str(task))
    assert running_assert["ansible.builtin.assert"]["that"] == [
        "_benchmark_run_client_status.stdout | trim == 'status: running'"
    ]

    execute = load_tasks("execute.yml")
    names = [task.get("name", "") for task in execute[0]["block"] + execute[0]["always"]]
    assert any("Archive the raw suite output" in name for name in names)
    assert any("Copy the raw result archive" in name for name in names)

    promptfoo_summary = next(
        task
        for task in execute[0]["always"]
        if task.get("name") == "Read a safe Promptfoo pass count from its JSON output"
    )
    assert promptfoo_summary["name"] == "Read a safe Promptfoo pass count from its JSON output"
    assert promptfoo_summary["ansible.builtin.command"]["argv"][4] == "/usr/local/bin/node"
    assert "results.stats.successes" in promptfoo_summary["ansible.builtin.command"]["argv"][-1]
    assert "passed" in promptfoo_summary["ansible.builtin.command"]["argv"][-1]
    safe_summary_task = next(
        task
        for task in execute[0]["always"]
        if task.get("name") == "Emit a safe suite summary to the Semaphore task log"
    )
    safe_summary = safe_summary_task["ansible.builtin.debug"]["msg"]
    assert set(safe_summary) == {
        "suite",
        "return_code",
        "stdout_line_count",
        "stderr_line_count",
        "promptfoo_pass_summary",
        "workload_id",
        "kv_arm",
        "cache_workload",
    }
    assert "_benchmark_run_promptfoo_summary.stdout" in safe_summary["promptfoo_pass_summary"]


def test_runner_upgrades_guest_first_and_publishes_patch_metadata() -> None:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    first_run_task = plays[2]["tasks"][0]["block"][0]
    assert first_run_task["ansible.builtin.import_tasks"] == "tasks/benchmark-run/upgrade.yml"
    assert first_run_task["when"] == "benchmark_run_request.upgrade | bool"

    preflight = load_tasks("preflight.yml")
    required = preflight[0]["ansible.builtin.assert"]["that"]
    assert any("gpu_guest_vmid" in check for check in required)
    assert any("default('true')" in check for check in required)
    request = next(task for task in preflight if task.get("ansible.builtin.add_host"))
    assert "false if suite in" in request["ansible.builtin.add_host"]["benchmark_run_request"]["upgrade"]

    upgrade = load_tasks("upgrade.yml")
    assert any("apt-get" in str(task.get("ansible.builtin.command", {}).get("argv", [])) for task in upgrade)
    assert any("full-upgrade" in str(task) for task in upgrade)
    package_upgrade = next(task for task in upgrade if task["name"] == "Upgrade all GPU guest packages")
    assert "not ansible_check_mode" in package_upgrade["when"]

    prepare = load_tasks("prepare.yml")
    assert any(task.get("ansible.builtin.import_tasks") == "patch-metadata.yml" for task in prepare)
    assert any("campaign-dimensions.json" in str(task) for task in prepare)
    # A skipped register is still defined; non-Promptfoo suites skip the items checksum.
    assert "_benchmark_run_items_checksum is not skipped" in str(prepare)
    metadata = load_tasks("patch-metadata.yml")
    assert any("pveversion" in str(task) for task in metadata)
    assert any("nvidia-smi" in str(task) for task in metadata)
    assert any("nvcc" in str(task) for task in metadata)
    patch_assert = next(task for task in metadata if "ansible.builtin.assert" in task)
    # ansible-core 2.19+ rejects a non-boolean conditional; regex_search returns the match string.
    for condition in patch_assert["ansible.builtin.assert"]["that"]:
        if "regex_search(" in condition:
            assert condition.endswith("is not none"), condition
    dimensions = next(
        task
        for task in metadata
        if "_benchmark_run_campaign_dimensions" in task.get("ansible.builtin.set_fact", {})
    )
    assert "patches" in str(dimensions["ansible.builtin.set_fact"])
    assert "moe_cpu_offload" in str(dimensions["ansible.builtin.set_fact"])
    assert "host_ram_limit_gib" in str(dimensions["ansible.builtin.set_fact"])
    assert "kv_pool_tokens" in str(dimensions["ansible.builtin.set_fact"])


def test_vllm_cell_uses_fixed_length_client_and_records_percentiles() -> None:
    tasks = load_tasks("vllm-serve.yml")
    argv = tasks[1]["ansible.builtin.set_fact"]["_benchmark_run_kvo_argv"]
    assert argv[:4] == ["/usr/bin/timeout", "--signal=TERM", "--kill-after=10s", "1200s"]
    assert argv[argv.index("--entrypoint") + 1] == "vllm"
    assert "--ignore-eos" in argv
    assert argv[argv.index("--metric-percentiles") + 1] == "50,90,99"
    assert "--save-detailed" in argv
    assert "--burstiness" in argv
    assert tasks[-1]["ansible.builtin.include_tasks"] == "cache-run-with-metrics.yml"

    prepare = load_tasks("prepare.yml")
    assert any("engine uptime" in task.get("name", "").lower() for task in prepare)
    assert any("host and engine uptime" in task.get("name", "").lower() for task in prepare)
