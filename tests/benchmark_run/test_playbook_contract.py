from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
PLAYBOOK = ROOT / "playbooks/benchmark-run.yml"
TASKS = ROOT / "playbooks/tasks/benchmark-run"


def load_tasks(name: str) -> list[dict[str, object]]:
    return yaml.safe_load((TASKS / name).read_text())


def test_runner_pins_tools_and_defaults_publishing_off() -> None:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    vars_ = plays[1]["vars"]
    assert vars_["_benchmark_run_evalscope_version"] == "1.13.0"
    assert vars_["_benchmark_run_promptfoo_version"] == "0.124.1"
    assert vars_["_benchmark_run_vllm_client_image"] == "vllm/vllm-openai-cpu:v0.31.0-x86_64"

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
    assert load_tasks("vllm-serve.yml")[0]["no_log"] is True

    promptfoo = load_tasks("promptfoo.yml")[0]["ansible.builtin.command"]["argv"]
    assert "--max-concurrency" in promptfoo
    assert "--no-cache" in promptfoo
    assert any("BENCHMARK_CHAT_TEMPLATE_KWARGS" in argument for argument in promptfoo)


def test_runner_checks_tagged_client_and_collects_raw_artifacts() -> None:
    prepare = load_tasks("prepare.yml")
    assert any("benchmark-client" in task.get("name", "") for task in prepare)
    assert any("engine" in task.get("name", "").lower() for task in prepare)

    execute = load_tasks("execute.yml")
    names = [task.get("name", "") for task in execute[0]["block"] + execute[0]["always"]]
    assert any("Archive the raw suite output" in name for name in names)
    assert any("Copy the raw result archive" in name for name in names)

    safe_summary = execute[0]["always"][0]["ansible.builtin.debug"]["msg"]
    assert set(safe_summary) == {
        "suite",
        "return_code",
        "stdout_line_count",
        "stderr_line_count",
    }


def test_vllm_cell_uses_fixed_length_client_and_records_percentiles() -> None:
    argv = load_tasks("vllm-serve.yml")[0]["ansible.builtin.command"]["argv"]
    assert argv[argv.index("--entrypoint") + 1] == "vllm"
    assert "--ignore-eos" in argv
    assert argv[argv.index("--metric-percentiles") + 1] == "50,90,99"
    assert "--save-detailed" in argv
    assert "--burstiness" in argv

    prepare = load_tasks("prepare.yml")
    assert any("engine uptime" in task.get("name", "").lower() for task in prepare)
    assert any("host and engine uptime" in task.get("name", "").lower() for task in prepare)
