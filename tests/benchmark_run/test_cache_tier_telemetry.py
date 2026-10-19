from pathlib import Path
import json
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).parents[2]
TASKS = ROOT / "playbooks/tasks/benchmark-run"


def load_tasks(name: str) -> list[dict[str, object]]:
    return yaml.safe_load((TASKS / name).read_text())


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
    result_import = next(task for task in always if task.get("name") ==
                         "Emit the KVO result JSON line to the Semaphore task log")
    assert result_import["ansible.builtin.import_tasks"] == "kvo-result.yml"

    result_tasks = load_tasks("kvo-result.yml")
    kvo_result = next(task for task in result_tasks if task.get("name") ==
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
    kvo_log = next(task for task in result_tasks if task.get("name") ==
                   "Emit the KVO result JSON line to the Semaphore task log")
    assert kvo_log["ansible.builtin.debug"]["msg"] == (
        "KVO_RESULT {{ _benchmark_run_kvo_result.stdout | trim }}"
    )


def test_kvo_result_aggregates_csv_and_metric_snapshots(tmp_path: Path) -> None:
    result_tasks = load_tasks("kvo-result.yml")
    result_task = next(task for task in result_tasks if task.get("name") ==
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
