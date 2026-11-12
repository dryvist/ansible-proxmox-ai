"""Check run-time inputs and preflight behavior for model campaigns."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, Template
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = REPO_ROOT / "playbooks"
TASKS = PLAYBOOKS / "tasks"
TARGET_PLAYBOOK = PLAYBOOKS / "llm-model-campaign-target.yml"
EVALSCOPE_OPTIONS = (
    "--model",
    "--api-url",
    "--api-key",
    "--eval-type",
    "--datasets",
    "--dataset-args",
    "--generation-config",
    "--eval-batch-size",
    "--sandbox",
    "--work-dir",
    "--no-timestamp",
)
LMEVAL_0413_TOP_LEVEL_HELP = """\
usage: lm-eval [-h] COMMAND ...

Language Model Evaluation Harness

positional arguments:
  COMMAND     Available commands
    run       Run the evaluation harness on specified tasks
    ls        List available tasks, groups, subtasks, or tags
    validate  Validate task configurations

options:
  -h, --help  show this help message and exit

quick start:
  # Basic evaluation
  lm-eval run --model hf --model_args pretrained=gpt2 --tasks hellaswag

legacy compatibility:
  The harness maintains backward compatibility with the original interface.
  If no command is specified, 'run' is automatically inserted:

  lm-eval --model hf --tasks hellaswag  # Equivalent to 'lm-eval run --model hf --tasks hellaswag'
"""
LMEVAL_0413_RUN_HELP = """\
usage: lm-eval run --model <model> --tasks <task> <task> --model_args <arg=value> <arg=value> [options]

model and tasks:
  --tasks, -t <task> [<task> ...]
  --model, -M <model>
  --model_args, -a <arg> [<arg> ...]
  --apply_chat_template [<template>]
  --limit, -L <limit>

evaluation settings:
  --num_fewshot, -f <n>
  --batch_size, -b <size>
  --gen_kwargs <arg> [<arg> ...]

data and output (see also: --limit):
  --output_path, -o <path>
  --log_samples, -s
  --samples, -E <json>  JSON mapping task names to sample indices, e.g.
                        '{"task1": [0,1,2]}'. Incompatible with --limit.

instruct formatting (see also: --apply_chat_template):
  --fewshot_as_multiturn [<bool>]

advanced options:
  --confirm_run_unsafe_code

logging and tracking:
  --metadata <args> [<args> ...]
"""


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _controller_tasks() -> list[dict[str, Any]]:
    plays = yaml.safe_load((PLAYBOOKS / "llm-model-campaign.yml").read_text(encoding="utf-8"))
    return plays[0]["tasks"]


def _named(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def test_vllm_kv_preflight_reads_the_current_serving_invocation():
    target = yaml.safe_load(TARGET_PLAYBOOK.read_text(encoding="utf-8"))[0]
    pre_tasks = target["pre_tasks"]
    unit_task = _named(pre_tasks, "Record the selected serving profile systemd unit")
    service_command = _named(pre_tasks, "Inspect the selected serving profile's rendered service command")
    invocation_read = _named(pre_tasks, "Read the selected vLLM profile's current systemd invocation ID")
    running_guard = _named(pre_tasks, "Require the selected vLLM profile service to be running")
    journal_read = _named(pre_tasks, "Read the selected vLLM profile startup log for live KV capacity")
    kv_preflight = _named(pre_tasks, "Require startup KV capacity and validate all vLLM reservations")

    unit = Template(unit_task["ansible.builtin.set_fact"]["_llm_campaign_serving_unit"]).render(
        _llm_campaign_role_defaults={"llm_gpu_serving_unit_prefix": "vllm-serving"},
        _llm_campaign_serving_profile="large",
    )
    assert unit == "vllm-serving-large.service"
    assert not any(character.isspace() for character in unit)
    assert service_command["ansible.builtin.command"]["argv"][-1] == "{{ _llm_campaign_serving_unit }}"
    assert invocation_read["ansible.builtin.command"]["argv"] == [
        "systemctl",
        "show",
        "--property",
        "InvocationID",
        "--value",
        "{{ _llm_campaign_serving_unit }}",
    ]

    journal_argv = journal_read["ansible.builtin.command"]["argv"]
    assert journal_argv[1] == "_SYSTEMD_INVOCATION_ID={{ _llm_campaign_vllm_invocation_id.stdout | trim }}"
    assert "--since" not in journal_argv
    assert "--unit" not in journal_argv

    invocation_condition = running_guard["ansible.builtin.assert"]["that"][0]
    rendered_condition = Template("{{ " + invocation_condition + " }}").render(
        _llm_campaign_vllm_invocation_id={"stdout": ""}
    )
    assert rendered_condition == "False"
    assert "not running" in running_guard["ansible.builtin.assert"]["fail_msg"]
    assert pre_tasks.index(running_guard) < pre_tasks.index(journal_read) < pre_tasks.index(kv_preflight)


def test_authenticated_recipes_resolve_runtime_model_and_key():
    tasks = _controller_tasks()
    controller_play = yaml.safe_load(
        (PLAYBOOKS / "llm-model-campaign.yml").read_text(encoding="utf-8")
    )[0]
    runtime = _named(tasks, "Require runtime endpoint inputs for authenticated model recipes")
    forward_key = _named(tasks, "Forward the campaign API key to the inventory target play")
    serving = _named(tasks, "Resolve the campaign serving profile")
    target_play = yaml.safe_load(TARGET_PLAYBOOK.read_text(encoding="utf-8"))[0]
    endpoint_tasks = _load_tasks(TASKS / "llm-model-campaign-endpoint-probe.yml")
    endpoint = _named(endpoint_tasks, "Verify the live endpoint advertises the expected model")
    model_check = _named(endpoint_tasks, "Require the live endpoint model ID to match this campaign pass")
    cell_tasks = _load_tasks(TASKS / "llm-model-campaign-cell.yml")
    command_block = _named(cell_tasks, "Run the tool-native command from the selected recipe row")
    command = command_block["block"][0]

    assert (
        "_llm_campaign_serving_model is defined"
        not in runtime["ansible.builtin.assert"]["that"]
    )
    assert any("_llm_campaign_serving_model | string is match(" in condition
               for condition in runtime["ansible.builtin.assert"]["that"])
    assert "_llm_campaign_api_key" in controller_play["vars"]
    assert any("_llm_campaign_api_key" in condition
               for condition in runtime["ansible.builtin.assert"]["that"])
    assert (
        "_llm_campaign_selected_artifacts[0].hf_repo"
        in serving["ansible.builtin.set_fact"]["_llm_campaign_serving_model"]
    )
    assert runtime["no_log"] is True
    assert forward_key["no_log"] is True
    assert "_llm_campaign_api_key_for_target" in forward_key["ansible.builtin.set_fact"]
    assert forward_key["when"] == "_llm_campaign_uses_api_key | bool"
    assert "_llm_campaign_api_key_for_target" in target_play["vars"]["_llm_campaign_api_key"]
    assert "Authorization" in endpoint["ansible.builtin.uri"]["headers"]
    assert "_llm_campaign_api_key" in endpoint["ansible.builtin.uri"]["headers"]
    assert endpoint["no_log"] == "{{ _llm_campaign_uses_api_key }}"
    assert model_check["no_log"] == "{{ _llm_campaign_uses_api_key }}"
    assert "_llm_campaign_api_key" in command["environment"]["OPENAI_API_KEY"]


def test_coding_and_reasoning_inputs_resolve_for_the_medium_artifact():
    tasks = _controller_tasks()
    survey = _named(tasks, "Validate the required survey values and safe config name")
    config_rule = next(
        condition
        for condition in survey["ansible.builtin.assert"]["that"]
        if condition.startswith("config_name is match(")
    )
    config_pattern = re.fullmatch(r"config_name is match\('(.+)'\)", config_rule).group(1)
    medium_artifacts = yaml.safe_load(
        (REPO_ROOT / "llm-models.d/67-gpu-artifacts-nvfp4-sweep.yml").read_text(encoding="utf-8")
    )["_llm_model_artifacts_nvfp4_sweep"]
    medium = next(
        artifact for artifact in medium_artifacts if artifact["artifact_id"] == "qwen38-27b-nvfp4"
    )
    serving = _named(tasks, "Resolve the campaign serving profile")
    parse = _named(tasks, "Parse positive concurrency and context values")
    serving_expression = serving["ansible.builtin.set_fact"]["_llm_campaign_serving_model"]
    context_expression = parse["ansible.builtin.set_fact"]["_llm_campaign_context_values"]
    environment = Environment()
    environment.tests["match"] = lambda value, pattern: re.match(pattern, str(value)) is not None

    for recipe in ("lm-eval/coding", "lm-eval/reasoning"):
        assert re.match(config_pattern, recipe)
        resolved_model = Template(serving_expression).render(
            serving_model=None,
            _llm_campaign_selected_artifacts=[medium],
        )
        resolved_context = environment.from_string(context_expression).render(context_list="8192")
        assert resolved_model == medium["hf_repo"]
        assert resolved_context.strip() == "[8192]"


def test_model_card_context_marker_resolves_from_the_model_registry():
    tasks = _controller_tasks()
    parse = _named(tasks, "Parse positive concurrency and context values")
    validate = _named(tasks, "Validate each survey list is canonical, positive, and unique")
    resolve = _named(tasks, "Resolve the model-card context marker using the recipe output reservation")

    assert "context_list.split" in parse["ansible.builtin.set_fact"]["_llm_campaign_context_tokens"]
    assert any("model_card_max" in condition for condition in validate["ansible.builtin.assert"]["that"])
    resolved = resolve["ansible.builtin.set_fact"]["_llm_campaign_context_values"]
    assert all(value in resolved for value in ("context_window_tokens", "output_tokens", "model_card_max"))


def test_lm_eval_preflight_checks_the_selected_execution_node():
    tasks = _load_tasks(TASKS / "llm-model-campaign-preflight-tool.yml")
    probe = _named(tasks, "Inspect the installed lm-eval command surface")
    options = _named(tasks, "Require every lm-eval option used by the selected recipe")
    code_guard = _named(tasks, "Require explicit lm-eval unsafe-code acknowledgement for code recipes")
    required_options = [
        condition.split("search('", 1)[1].split("')", 1)[0]
        for condition in options["ansible.builtin.assert"]["that"]
    ]

    assert "localhost" in probe["delegate_to"]
    assert probe["ansible.builtin.command"]["argv"] == ["lm_eval", "run", "--help"]
    assert "If no command is specified, 'run' is automatically inserted:" in LMEVAL_0413_TOP_LEVEL_HELP
    assert all(option in LMEVAL_0413_RUN_HELP for option in required_options)
    assert any("--confirm_run_unsafe_code" in condition for condition in code_guard["ansible.builtin.assert"]["that"])
    assert "--confirm_run_unsafe_code" in LMEVAL_0413_RUN_HELP


def test_lm_eval_is_installed_for_selected_vllm_campaign_targets():
    target = yaml.safe_load(TARGET_PLAYBOOK.read_text(encoding="utf-8"))[0]
    pre_tasks = target["pre_tasks"]
    install = _named(pre_tasks, "Install lm-eval for selected vLLM benchmark-target recipes")
    preflight = _named(pre_tasks, "Check every configured executable before cache changes or benchmarks")
    role_tasks = _load_tasks(REPO_ROOT / "roles/nvidia_gpu_guest/tasks/install-lm-eval.yml")
    defaults_path = REPO_ROOT / "roles/nvidia_gpu_guest/defaults/main/00-core.yml"
    defaults_text = defaults_path.read_text(encoding="utf-8")
    defaults = yaml.safe_load(defaults_text)
    probe_tasks = _load_tasks(TASKS / "llm-model-campaign-preflight-tool.yml")
    probe = _named(probe_tasks, "Inspect the installed lm-eval command surface")
    site = yaml.safe_load((PLAYBOOKS / "site.yml").read_text(encoding="utf-8"))
    campaign = _named(site, "Run a named model benchmark campaign")

    assert install["ansible.builtin.include_role"] == {
        "name": "nvidia_gpu_guest",
        "tasks_from": "install-lm-eval.yml",
    }
    assert install["when"][0] == "engine == 'vllm'"
    assert "selectattr('executable', 'equalto', 'lm_eval')" in install["when"][1]
    assert "selectattr('run_on', 'equalto', 'benchmark_target')" in install["when"][1]
    assert pre_tasks.index(install) < pre_tasks.index(preflight)
    assert role_tasks[1]["ansible.builtin.command"]["argv"][-1] == (
        "lm-eval[api]=={{ nvidia_gpu_guest_lm_eval_version }}"
    )
    assert role_tasks[1]["environment"] == "{{ nvidia_gpu_guest_uv_environment }}"
    assert role_tasks[2]["ansible.builtin.file"]["src"] == (
        "{{ nvidia_gpu_guest_uv_tool_bin_dir }}/lm_eval"
    )
    assert role_tasks[2]["ansible.builtin.file"]["dest"] == "/usr/local/bin/lm_eval"
    assert probe["ansible.builtin.command"]["argv"] == ["lm_eval", "run", "--help"]
    assert defaults["nvidia_gpu_guest_lm_eval_version"] == "0.4.13"
    assert "# renovate: datasource=pypi depName=lm-eval" in defaults_text
    assert "model_campaign" in campaign["tags"]


def test_evalscope_preflight_checks_each_recipe_option_and_the_sandbox_engine():
    tasks = _load_tasks(TASKS / "llm-model-campaign-preflight-tool.yml")
    surface = _named(tasks, "Inspect the installed EvalScope command surface")
    options = _named(tasks, "Require every EvalScope option used by the selected recipe")
    docker = _named(tasks, "Require a running Docker engine for the EvalScope code sandbox")

    assert surface["ansible.builtin.command"]["argv"] == ["evalscope", "eval", "--help"]
    asserted = options["ansible.builtin.assert"]["that"]
    assert asserted == [f"_llm_campaign_evalscope_help.stdout is search('{option}')" for option in EVALSCOPE_OPTIONS]
    assert docker["ansible.builtin.command"]["argv"] == ["docker", "info"]
    for task in (surface, options):
        assert task["when"] == "_llm_campaign_run.executable == 'evalscope'"
    assert docker["when"][0] == "_llm_campaign_run.executable == 'evalscope'"
    assert '"engine": "docker"' in docker["when"][1]


def test_lm_eval_results_are_found_on_the_run_executor():
    tasks = _load_tasks(TASKS / "llm-model-campaign-cell.yml")
    find = _named(tasks, "Find lm-eval result and sample files for its converter")
    fetch = _named(tasks, "Fetch lm-eval result and sibling sample files for conversion")

    assert find["ansible.builtin.find"]["recurse"] is True
    assert "localhost" in find["delegate_to"]
    assert "_llm_campaign_run.run_on != 'controller'" in fetch["when"]


def test_cell_command_timeout_and_keyed_no_log():
    tasks = _load_tasks(TASKS / "llm-model-campaign-cell.yml")
    command_block = _named(tasks, "Run the tool-native command from the selected recipe row")
    command = _named(command_block["block"], "Execute the selected recipe command")
    failure = _named(command_block["rescue"], "Report the failed model campaign cell command")
    target = yaml.safe_load(TARGET_PLAYBOOK.read_text(encoding="utf-8"))[0]
    timeout_seconds = target["vars"]["llm_model_campaign_cell_timeout_seconds"]
    fail_msg = failure["ansible.builtin.fail"]["msg"]

    assert "OPENAI_API_KEY" in command["environment"]
    assert "HF_ALLOW_CODE_EVAL" in command["environment"]
    assert command["async"] == "{{ llm_model_campaign_cell_timeout_seconds }}"
    assert command["poll"] == 1
    assert timeout_seconds == 540
    assert timeout_seconds < 600
    assert command["no_log"] == "{{ _llm_campaign_run.api_key_env is defined }}"
    assert "llm_model_campaign_cell_timeout_seconds" in fail_msg
    assert "_llm_campaign_command_result.stdout" not in fail_msg
