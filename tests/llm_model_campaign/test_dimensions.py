"""Check campaign preflight and controller contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = REPO_ROOT / "playbooks"
TASKS = PLAYBOOKS / "tasks"
DIMENSIONS_TEMPLATE = PLAYBOOKS / "templates" / "llm-model-campaign-dimensions.json.j2"
TARGET_PLAYBOOK = PLAYBOOKS / "llm-model-campaign-target.yml"
POWER_LIMIT_FIXTURE = Path(__file__).parent / "fixtures/nvidia-smi-enforced-power-limit.csv"

DECLARED_CONFIGS = (
    "llama-cpp/cross-card",
    "vllm/cross-card",
    "mlx/cross-card",
    "mlx/vlm-native",
    "mlx/vlm-native-mtp",
    "mlx/vllm-mlx",
    "lm-eval/quick-intelligence",
    "lm-eval/gpqa-diamond",
    "lm-eval/ruler",
    "lm-eval/coding",
    "lm-eval/reasoning",
    "evalscope/livecodebench",
)
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


def _target_power_tasks() -> list[dict[str, Any]]:
    plays = yaml.safe_load(TARGET_PLAYBOOK.read_text(encoding="utf-8"))
    block = _named(
        plays[0]["pre_tasks"], "Verify the declared power cap against the target GPU or platform"
    )
    return block["block"]


def _named(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def test_survey_pattern_admits_every_declared_config_and_nothing_else():
    survey = _named(_controller_tasks(), "Validate the required survey values and safe config name")
    expression = next(
        item for item in survey["ansible.builtin.assert"]["that"] if item.startswith("config_name is match(")
    )
    parsed = re.fullmatch(r"config_name is match\('(.+)'\)", expression)
    assert parsed is not None, expression
    pattern = parsed.group(1)

    for name in DECLARED_CONFIGS:
        assert re.match(pattern, name), name
    for name in ("other/cross-card", "evalscope/Upper", "evalscope/../escape", "evalscope/a/b"):
        assert not re.match(pattern, name), name


def test_pinned_converter_must_accept_the_dimensions_flag_before_target_work():
    tasks = _controller_tasks()
    names = [task["name"] for task in tasks]
    probe = _named(tasks, "Verify the envelope converter is available before target work")
    check = _named(tasks, "Require the converter to accept the campaign dimensions file")
    assertion = check["ansible.builtin.assert"]

    assert probe["register"] == "_llm_campaign_publish_help"
    assert probe["failed_when"] is False
    assert assertion["that"] == [
        "_llm_campaign_publish_help.rc == 0",
        "_llm_campaign_publish_help.stdout is search('--campaign-dimensions')",
    ]
    assert "_llm_campaign_benchmarks_ref" in assertion["fail_msg"]
    assert "binary uv" in assertion["fail_msg"]
    assert names.index(check["name"]) == names.index(probe["name"]) + 1


def test_runner_missing_uv_result_fails_preflight_with_a_named_binary():
    result = yaml.safe_load((Path(__file__).parent / "fixtures/runner-missing-uv.yml").read_text(encoding="utf-8"))
    probe = _named(_controller_tasks(), "Verify the envelope converter is available before target work")
    check = _named(_controller_tasks(), "Require the converter to accept the campaign dimensions file")

    assert result == {"rc": 2, "stdout": "", "stderr": ""}
    assert probe["failed_when"] is False
    assert check["ansible.builtin.assert"]["that"][0] == "_llm_campaign_publish_help.rc == 0"
    assert "binary uv" in check["ansible.builtin.assert"]["fail_msg"]


def test_target_play_receives_the_pinned_config_revision():
    saved = _named(_controller_tasks(), "Save the prepared campaign inputs for the inventory target play")

    assert saved["ansible.builtin.set_fact"]["_llm_campaign_config_ref"] == "{{ _llm_campaign_benchmarks_ref }}"


def test_power_guard_and_result_writer_use_the_live_enforced_limit_fixture():
    output = POWER_LIMIT_FIXTURE.read_text(encoding="utf-8").strip()
    gpu = [value.strip() for value in output.split(",")]
    assert len(gpu) == 4
    assert gpu[3] == "200.00"

    tasks = _target_power_tasks()
    query = _named(tasks, "Read the target GPU enforced power limit")
    assert "enforced.power.limit" in query["ansible.builtin.command"]["argv"][1]
    guard = _named(tasks, "Assert the survey cap matches target telemetry")
    expression = guard["ansible.builtin.assert"]["that"][0]
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.filters["split"] = lambda value, separator=None: value.split(separator)
    environment.filters["unique"] = lambda values: list(dict.fromkeys(values))
    evaluate = environment.compile_expression(expression)
    target_read = {"rc": 0, "stdout_lines": [output]}
    assert evaluate(
        _llm_campaign_power_limit=target_read,
        _llm_campaign_system_name={"stdout": ""},
        engine="vllm",
        power_cap_w=float(gpu[3]),
    )
    assert not evaluate(
        _llm_campaign_power_limit=target_read,
        _llm_campaign_system_name={"stdout": ""},
        engine="vllm",
        power_cap_w=290,
    )

    capture = _named(tasks, "Record target GPU metadata for the existing envelope converter")
    assert "stdout_lines[0].split(',')" in capture["ansible.builtin.set_fact"]["_llm_campaign_gpu_metadata"]
    convert = _named(
        _load_tasks(TASKS / "llm-model-campaign-convert.yml"),
        "Convert the supported tool output to its versioned result envelope",
    )
    power_env = convert["environment"]["MLX_BENCH_POWER_LIMIT_W"]
    assert "_llm_campaign_gpu_metadata[3]" in power_env
    assert "power_cap_w" not in power_env
    assert environment.from_string(power_env).render(_llm_campaign_gpu_metadata=gpu) == gpu[3]
    assert "'power_cap_w': (gpu[3] | float) if gpu else none" in DIMENSIONS_TEMPLATE.read_text(encoding="utf-8")


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


def test_lm_eval_results_are_found_below_the_per_model_directory():
    tasks = _load_tasks(TASKS / "llm-model-campaign-cell.yml")
    find = _named(tasks, "Find lm-eval result and sample files for its converter")

    assert find["ansible.builtin.find"]["recurse"] is True


def test_conversion_renders_the_dimensions_file_and_passes_it_to_the_converter():
    render, convert = _load_tasks(TASKS / "llm-model-campaign-convert.yml")
    template = render["ansible.builtin.template"]

    assert template["src"].endswith("/templates/llm-model-campaign-dimensions.json.j2")
    assert template["dest"].endswith("{{ _llm_campaign_cell_slug }}.dimensions.json")
    assert render["delegate_to"] == "localhost"
    argv = convert["ansible.builtin.command"]["argv"]
    assert "'--campaign-dimensions'" in argv
    assert "'.dimensions.json'" in argv
    assert convert["delegate_to"] == "localhost"
