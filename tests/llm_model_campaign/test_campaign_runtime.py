"""Check run-time inputs and preflight behavior for model campaigns."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _controller_tasks() -> list[dict[str, Any]]:
    plays = yaml.safe_load((PLAYBOOKS / "llm-model-campaign.yml").read_text(encoding="utf-8"))
    return plays[0]["tasks"]


def _named(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def test_authenticated_recipes_require_runtime_alias_and_key():
    tasks = _controller_tasks()
    runtime = _named(tasks, "Require runtime endpoint inputs for authenticated model recipes")
    target = yaml.safe_load(TARGET_PLAYBOOK.read_text(encoding="utf-8"))[0]
    endpoint_tasks = _load_tasks(TASKS / "llm-model-campaign-endpoint-probe.yml")
    endpoint = _named(endpoint_tasks, "Verify the live endpoint advertises the expected model")
    model_check = _named(endpoint_tasks, "Require the live endpoint model ID to match this campaign pass")
    saved = _named(tasks, "Save the prepared campaign inputs for the inventory target play")

    assert "serving_model is defined" in runtime["ansible.builtin.assert"]["that"]
    assert "lookup('env', 'OPENAI_API_KEY') | length > 0" in runtime["ansible.builtin.assert"]["that"]
    assert runtime["no_log"] is True
    assert "Authorization" in endpoint["ansible.builtin.uri"]["headers"]
    assert endpoint["no_log"] == "{{ _llm_campaign_uses_api_key }}"
    assert model_check["no_log"] == "{{ _llm_campaign_uses_api_key }}"
    assert "_llm_campaign_serving_model" in saved["ansible.builtin.set_fact"]


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

    assert "localhost" in probe["delegate_to"]
    assert "--metadata" in options["ansible.builtin.assert"]["that"][-1]
    assert any("--confirm_run_unsafe_code" in condition for condition in code_guard["ansible.builtin.assert"]["that"])


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


def test_keyed_controller_commands_keep_runtime_credentials_out_of_task_output():
    tasks = _load_tasks(TASKS / "llm-model-campaign-cell.yml")
    command = _named(tasks, "Run the tool-native command from the selected recipe row")

    assert "OPENAI_API_KEY" in command["environment"]
    assert "HF_ALLOW_CODE_EVAL" in command["environment"]
    assert command["no_log"] == "{{ _llm_campaign_run.api_key_env is defined }}"
