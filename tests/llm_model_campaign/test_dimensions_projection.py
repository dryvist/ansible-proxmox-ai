"""Check campaign dimensions are projected from the live target context."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jinja2
import pytest
import yaml
from jinja2.nativetypes import NativeEnvironment

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = REPO_ROOT / "playbooks"
TASKS = PLAYBOOKS / "tasks"
DIMENSIONS_TEMPLATE = PLAYBOOKS / "templates" / "llm-model-campaign-dimensions.json.j2"


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _named(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def _regex_search(value: str, pattern: str, *groups: str) -> Any:
    """Ansible's filter: the match, or the listed back-references as a list; None without a match."""
    match = re.search(pattern, value)
    if match is None:
        return None
    if not groups:
        return match.group()
    return [match.group(int(group.removeprefix("\\"))) for group in groups]


def _render(context: dict[str, Any]) -> dict[str, Any]:
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.filters["regex_search"] = _regex_search
    environment.filters["to_nice_json"] = lambda value: json.dumps(value, indent=4, sort_keys=True)
    template = environment.from_string(DIMENSIONS_TEMPLATE.read_text(encoding="utf-8"))
    return json.loads(template.render(**context))


def _render_converter_argv(model: dict[str, Any]) -> list[str]:
    convert = _named(
        _load_tasks(TASKS / "llm-model-campaign-convert.yml"),
        "Convert the supported tool output to its versioned result envelope",
    )
    template = convert["ansible.builtin.command"]["argv"]
    context = {
        "_llm_campaign_converter_file": {"path": "/tmp/results.json"},
        "_llm_campaign_run": {"converter": {"kind": "throughput", "suite": "campaign"}},
        "_llm_campaign_model": model,
        "_llm_campaign_serving_model": "example-org/example-model",
        "llm_model_campaign_pass_hf_repo": "example-org/example-model",
        "llm_model_campaign_pass_serving_model": "example-org/example-model",
        "llm_model_campaign_pass_name": "campaign",
        "_llm_campaign_port": 8000,
        "engine": "vllm",
        "_llm_campaign_concurrency_value": 4,
        "model_size": "size-medium",
        "_llm_campaign_context_value": 8192,
        "_llm_campaign_gpu_metadata": ["Example GPU", "97887", "100.1", "300.00"],
        "power_cap_w": "300",
        "_llm_campaign_cell_controller_dir": "/tmp",
        "_llm_campaign_cell_slug": "example-cell",
        "_llm_campaign_repetition_value": 1,
    }

    argv = NativeEnvironment(undefined=jinja2.StrictUndefined).from_string(template).render(**context)
    assert isinstance(argv, list)
    return argv


def _cell(**overrides: Any) -> dict[str, Any]:
    context: dict[str, Any] = {
        "engine": "vllm",
        "config_name": "vllm/cross-card",
        "power_cap_w": "300",
        "ansible_facts": {
            "distribution": "ExampleOS",
            "distribution_version": "13",
            "kernel": "9.9.9-example",
            "architecture": "x86_64",
            "system": "Linux",
            "memtotal_mb": 98304,
            "processor": ["0", "ExampleVendor", "Example CPU", "1", "ExampleVendor", "Example CPU"],
        },
        "_llm_campaign_gpu_metadata": ["Example GPU", "97887", "100.1", "300.00"],
        "_llm_campaign_model": {
            "artifact_id": "example-artifact",
            "hf_repo": "example-org/example-model",
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "quantization": "ExampleQuant",
        },
        "_llm_campaign_run": {"name": "example-row", "output_tokens": 256, "repetitions": 2},
        "_llm_campaign_concurrency_value": 4,
        "_llm_campaign_started_utc": {"stdout": "2026-10-05T22:10:00Z"},
        "_llm_campaign_workdir": "/tmp/example-workspace",
        "_llm_campaign_cell_slug": "example-row-ctx8192-c4-r1",
        "_llm_campaign_service_command": {"stdout": "/usr/bin/vllm serve x --max-model-len 196608 --port 1"},
        "hostvars": {"localhost": {"_llm_campaign_config_ref": "f" * 40}},
    }
    context.update(overrides)
    return context


def test_gpu_cell_dimensions_carry_only_attested_leaves():
    assert _render(_cell()) == {
        "campaign_dimensions": {
            "hardware": {
                "accelerator_memory_gb": 96,
                "accelerator_model": "Example GPU",
                "host_cpu": "Example CPU",
                "host_ram_gb": 96,
                "power_cap_w": 300.0,
            },
            "software": {
                "backend": "CUDA",
                "driver_version": "100.1",
                "engine": "vLLM",
                "kernel": "9.9.9-example",
                "operating_system": "ExampleOS 13",
            },
            "model": {
                "hf_repo": "example-org/example-model",
                "id": "example-artifact",
                "quantization": "ExampleQuant",
                "revision_sha": "0123456789abcdef0123456789abcdef01234567",
            },
            "run": {
                "allocated_context_tokens": 196608,
                "concurrent_agents": 4,
                "output_tokens": 256,
                "repeats": 2,
            },
            "provenance": {
                "config_git_sha": "f" * 40,
                "config_name": "vllm/cross-card",
                "run_id": "example-workspace-example-row-ctx8192-c4-r1",
                "timestamp_utc": "2026-10-05T22:10:00Z",
            },
        }
    }


def test_apple_cell_has_no_accelerator_cap_or_service_window():
    cell = _cell(
        engine="mlx_lm",
        power_cap_w="0",
        ansible_facts={
            "distribution": "ExampleOS",
            "distribution_version": "26",
            "kernel": "25.0.0",
            "architecture": "arm64",
            "system": "Darwin",
            "memtotal_mb": 131072,
            "processor": ["Example Silicon"],
        },
        _llm_campaign_service_command={"skipped": True},
    )
    del cell["_llm_campaign_gpu_metadata"]
    del cell["_llm_campaign_model"]["revision"]

    dimensions = _render(cell)["campaign_dimensions"]

    assert dimensions["hardware"] == {
        "chassis_container": "macos",
        "host_cpu": "Example Silicon",
        "host_ram_gb": 128,
    }
    assert dimensions["software"]["engine"] == "MLX"
    assert dimensions["software"]["backend"] == "Metal"
    assert "revision_sha" not in dimensions["model"]
    assert "allocated_context_tokens" not in dimensions["run"]


@pytest.mark.parametrize(
    ("engine", "unit", "window"),
    [
        ("vllm", "vllm serve x --max-model-len=65536", 65536),
        ("llama_cpp", "llama-server -m x --ctx-size 262144 --port 1", 262144),
        ("llama_cpp", "llama-server -m x --port 1", None),
        ("vllm", "llama-server --ctx-size 4096", None),
    ],
)
def test_allocated_context_is_the_live_service_window_for_its_engine(engine, unit, window):
    run = _render(_cell(engine=engine, _llm_campaign_service_command={"stdout": unit}))["campaign_dimensions"]["run"]

    assert run.get("allocated_context_tokens") == window


@pytest.mark.parametrize(
    ("engine", "label"),
    [("vllm", "vLLM"), ("llama_cpp", "llama.cpp"), ("mlx_lm", "MLX")],
)
def test_engine_selector_maps_to_the_schema_engine_name(engine, label):
    assert _render(_cell(engine=engine))["campaign_dimensions"]["software"]["engine"] == label


def test_no_leaf_is_written_as_null_and_only_known_groups_appear():
    groups = _render(_cell())["campaign_dimensions"]

    assert set(groups) <= {"hardware", "software", "model", "run", "provenance"}
    assert all(value is not None for group in groups.values() for value in group.values())


@pytest.mark.parametrize(
    ("metadata", "expected_tags"),
    [
        (
            {"model_task": "text-generation", "model_task_source": "model_card"},
            ["model_task=text-generation", "model_task_source=model_card"],
        ),
        ({}, []),
    ],
)
def test_model_task_metadata_uses_supported_converter_tags(metadata, expected_tags):
    model = {**_cell()["_llm_campaign_model"], **metadata}
    dimensions = _render(_cell(_llm_campaign_model=model))
    argv = _render_converter_argv(model)
    converter_tags = [
        argv[index + 1] for index, argument in enumerate(argv[:-1]) if argument == "--tag"
    ]

    assert set(dimensions) == {"campaign_dimensions"}
    assert [tag for tag in converter_tags if tag.startswith("model_task")] == expected_tags
