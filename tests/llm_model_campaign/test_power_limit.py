"""Check the campaign power guard against target telemetry."""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = REPO_ROOT / "playbooks"
TASKS = PLAYBOOKS / "tasks"
DIMENSIONS_TEMPLATE = PLAYBOOKS / "templates" / "llm-model-campaign-dimensions.json.j2"
TARGET_PLAYBOOK = PLAYBOOKS / "llm-model-campaign-target.yml"
POWER_LIMIT_FIXTURE = Path(__file__).parent / "fixtures/nvidia-smi-enforced-power-limit.csv"


def _named(tasks: list[dict], name: str) -> dict:
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def _target_power_tasks() -> list[dict]:
    plays = yaml.safe_load(TARGET_PLAYBOOK.read_text(encoding="utf-8"))
    block = _named(plays[0]["pre_tasks"], "Verify the declared power cap against the target GPU or platform")
    return block["block"]


def _load_tasks(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
