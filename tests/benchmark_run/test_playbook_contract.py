from pathlib import Path
import json
import re
import subprocess
import sys

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
    assert tasks[-1]["ansible.builtin.import_tasks"] == "cache-run-with-metrics.yml"

    prepare = load_tasks("prepare.yml")
    assert any("engine uptime" in task.get("name", "").lower() for task in prepare)
    assert any("host and engine uptime" in task.get("name", "").lower() for task in prepare)


def test_cache_tier_cells_use_upstream_tools_and_preserve_telemetry() -> None:
    preflight = load_tasks("preflight.yml")
    cache_validation = next(task for task in preflight if task["name"] == "Validate cache-tier benchmark inputs")
    assertions = cache_validation["ansible.builtin.assert"]["that"]
    assert any("user_history_tokens | int >= 32768" in assertion for assertion in assertions)
    assert any("num_rounds | int >= 6" in assertion for assertion in assertions)
    assert any("answer_tokens | int == 512" in assertion for assertion in assertions)
    assert any("workload_id == 'W2'" in assertion for assertion in assertions)

    lmcache = load_tasks("lmcache-multi-round-qa.yml")
    lmcache_argv = lmcache[0]["ansible.builtin.set_fact"]["_benchmark_run_kvo_argv"]
    assert "/metrics" in lmcache[0]["ansible.builtin.set_fact"]["_benchmark_run_kvo_metrics_url"]
    assert "/work/kvo/lmcache/multi-round-qa.py" in lmcache_argv
    assert "/usr/bin/timeout" in lmcache_argv
    assert "--shared-system-prompt" in lmcache_argv
    assert "--user-history-prompt" in lmcache_argv
    assert "--enforce-strict-concurrent-users" in lmcache_argv
    assert "--time" in lmcache_argv

    vllm = load_tasks("vllm-multi-turn.yml")
    vllm_argv = vllm[0]["ansible.builtin.set_fact"]["_benchmark_run_kvo_argv"]
    assert "/metrics" in vllm[0]["ansible.builtin.set_fact"]["_benchmark_run_kvo_metrics_url"]
    assert "/work/kvo/vllm/benchmark_serving_multi_turn.py" in vllm_argv
    assert "/usr/bin/timeout" in vllm_argv
    assert "--stats-json-output" in vllm_argv
    assert "--max-turns" in vllm_argv
    assert "--max-active-conversations" in vllm_argv

    sampler = load_tasks("cache-run-with-metrics.yml")[0]["ansible.builtin.shell"]["cmd"]
    for expected in (
        "next_sample=$((SECONDS + 15))",
        "next_sample=$((next_sample + 15))",
        "sleep 1",
        "metrics_url",
        "memory.current",
        "memory.max",
        "memory.peak",
        "systemctl list-units",
        "VmRSS:",
        "nvidia-smi",
        "gpu_memory_used_mib",
        "capture_sample before",
        "capture_sample after",
    ):
        assert expected in sampler
    rendered_shell = re.sub(r"{{.*?}}", "safe", sampler, flags=re.DOTALL)
    subprocess.run(["bash", "-n"], input=rendered_shell, check=True, text=True)

    sources = load_tasks("cache-tools.yml")
    assert any("LMCache/LMCache/v" in str(task) and "multi_round_qa" in str(task) for task in sources)
    assert any("vllm-project/vllm/v" in str(task) and "multi_turn" in str(task) for task in sources)
    assert any("--help" in task.get("ansible.builtin.command", {}).get("argv", []) for task in sources)
    assert any("tooling.json" in str(task) for task in sources)

    prepare = load_tasks("prepare.yml")
    metrics_probe = next(task for task in prepare if task.get("register") == "_benchmark_run_kvo_metrics_probe")
    memory_probe = next(task for task in prepare if task.get("register") == "_benchmark_run_kvo_memory_probe")
    assert "/metrics" in str(metrics_probe)
    assert "memory.current" in str(memory_probe)
    for probe in (metrics_probe, memory_probe):
        assert "lmcache-multi-round-qa" in str(probe.get("when"))
        assert "vllm-multi-turn" in str(probe.get("when"))

    execute = load_tasks("execute.yml")[0]
    always = execute["always"]
    assert any("Copy cache-cell telemetry snapshots" in task.get("name", "") for task in always)
    assert any("Archive the raw suite output" in task.get("name", "") for task in always)
    kvo_result = next(task for task in always if task.get("name") ==
                      "Summarize the LMCache cache-tier cell from its CSV and metric snapshots")
    result_argv = kvo_result["ansible.builtin.command"]["argv"]
    result_code = result_argv[result_argv.index("-c") + 1]
    for expected in (
        "question_id",
        "ttft_p99_ms",
        "generation_time",
        "vllm:cache_config_info",
        "num_gpu_blocks",
        "block_size",
        "prefix_cache_queries",
        "prefix_cache_hits",
        "kv_cache_usage_perc",
        "preempt",
        "external_prefix_cache_hit_rate",
        "gpu_guest_cgroup_memory_max_gib",
        "gpu_guest_cgroup_memory_peak_gib",
        "vllm_process_rss_peak_gib",
    ):
        assert expected in result_code
    assert "benchmark_run_request.run_id" in str(result_argv)
    assert kvo_result["when"] == [
        "benchmark_run_request.suite == 'lmcache-multi-round-qa'",
        "_benchmark_run_suite.rc | default(1) == 0",
    ]
    kvo_log = next(task for task in always if task.get("name") ==
                   "Emit the KVO result JSON line to the Semaphore task log")
    assert kvo_log["ansible.builtin.debug"]["msg"] == (
        "KVO_RESULT {{ _benchmark_run_kvo_result.stdout | trim }}"
    )


def test_kvo_result_aggregates_csv_and_metric_snapshots(tmp_path: Path) -> None:
    execute = load_tasks("execute.yml")[0]
    result_task = next(task for task in execute["always"] if task.get("name") ==
                       "Summarize the LMCache cache-tier cell from its CSV and metric snapshots")
    argv = result_task["ansible.builtin.command"]["argv"]
    code = argv[argv.index("-c") + 1].replace("'/out'", repr(str(tmp_path)))
    run_id = "fixture-run"
    (tmp_path / "telemetry").mkdir()
    (tmp_path / f"{run_id}-summary.csv").write_text(
        "prompt_tokens,generation_tokens,ttft,generation_time,question_id,launch_time,finish_time\n"
        "100,10,0.1,1.0,1,1.0,2.0\n"
        "100,10,0.2,1.0,1,2.0,3.0\n"
        "200,20,0.3,2.0,2,3.0,5.0\n"
        "200,20,0.5,2.0,2,4.0,6.0\n",
        encoding="utf-8",
    )
    metric_lines = {
        "before": """vllm:cache_config_info{num_gpu_blocks=\"10\",block_size=\"256\"} 1
vllm:prefix_cache_queries 0
vllm:prefix_cache_hits 0
lmcache:external_prefix_cache_hits_total 1
lmcache:external_prefix_cache_queries_total 2
vllm:kv_cache_usage_perc 0.25
vllm:num_preemptions_total 1
""",
        "after": """vllm:cache_config_info{num_gpu_blocks=\"10\",block_size=\"256\"} 1
vllm:prefix_cache_queries 100
vllm:prefix_cache_hits 50
lmcache:external_prefix_cache_hits_total 6
lmcache:external_prefix_cache_queries_total 12
vllm:kv_cache_usage_perc 0.80
vllm:num_preemptions_total 4
""",
    }
    for phase, contents in metric_lines.items():
        (tmp_path / "telemetry" / f"{run_id}-{phase}-20261018T000000.prom").write_text(
            contents, encoding="utf-8"
        )
    (tmp_path / "telemetry" / f"{run_id}-cgroup-memory.tsv").write_text(
        "timestamp_utc\tphase\tmemory_current_bytes\tgpu_memory_used_mib\t"
        "memory_max_bytes\tmemory_peak_bytes\tvllm_main_process_rss_kib\n"
        "2026-10-18T00:00:00Z\tbefore\t1073741824\t1000\t68719476736\t2147483648\t1048576\n"
        "2026-10-18T00:00:15Z\tduring\t2147483648\t2000\t68719476736\t3221225472\t2097152\n",
        encoding="utf-8",
    )
    cfg = {
        "run_id": run_id,
        "suite": "lmcache-multi-round-qa",
        "kv_arm": "A0",
        "workload_id": "W1",
        "host_ram_limit_gib": 64,
        "prefix_caching": True,
        "kv_offload_backend": "none",
        "kv_offload_cpu_gib": 0,
        "kv_offload_disk_gib": 0,
        "weight_cpu_offload_gib": 0,
        "llamacpp_cache_ram_mib": 0,
        "gpu_memory_utilization": 0.85,
        "max_num_batched_tokens": 8192,
        "cache_workload": "fits_gpu",
        "num_users": 2,
        "num_rounds": 2,
        "shared_prompt_tokens": 2048,
        "user_history_tokens": 32768,
        "answer_tokens": 512,
        "request_rate": 0.1,
        "duration_seconds": 180,
        "seed": 42,
    }
    completed = subprocess.run(
        [sys.executable, "-c", code, json.dumps(cfg), f"{run_id}-summary.csv"],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["gpu_kv_capacity_tokens"] == 2560
    assert result["ttft_first_round_p50_ms"] == 150
    assert result["ttft_later_rounds_p99_ms"] == 498
    assert result["prefix_cache_hit_rate"] == 0.5
    assert result["external_prefix_cache_hits"] == 5
    assert result["external_prefix_cache_hit_rate"] == 0.5
    assert result["kv_cache_usage_peak"] == 0.8
    assert result["preemptions"] == 3
    assert result["gpu_guest_cgroup_memory_peak_gib"] == 3
    assert result["vllm_process_rss_peak_gib"] == 2
