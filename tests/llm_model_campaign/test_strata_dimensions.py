"""Check the campaign dimensions emitted for Strata runs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jinja2

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "playbooks/templates/llm-model-campaign-dimensions.json.j2"


def _regex_search(value: str, pattern: str, *groups: str) -> Any:
    match = re.search(pattern, value)
    if match is None:
        return None
    if not groups:
        return match.group()
    return [match.group(int(group.removeprefix("\\"))) for group in groups]


def test_strata_dimensions_record_engine_version_and_batch_shape():
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.filters["regex_search"] = _regex_search
    environment.filters["to_nice_json"] = lambda value: json.dumps(value, indent=2)
    context = {
        "engine": "strata",
        "config_name": "strata/throughput",
        "power_cap_w": "300",
        "ansible_facts": {
            "distribution": "ExampleOS",
            "distribution_version": "13",
            "kernel": "9.9.9-example",
            "architecture": "x86_64",
            "system": "Linux",
            "virtualization_type": "lxc",
            "memtotal_mb": 98304,
            "processor": ["ExampleVendor", "Example CPU"],
        },
        "_llm_campaign_gpu_metadata": ["Example GPU", "97887", "100.1", "300.00"],
        "_llm_campaign_model": {
            "artifact_id": "example-artifact",
            "hf_repo": "example-org/example-model",
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "quantization": "ExampleQuant",
        },
        "_llm_campaign_run": {"name": "example-row", "output_tokens": 512, "repetitions": 2},
        "_llm_campaign_context_value": 25,
        "_llm_campaign_strata_context_tokens": 65536,
        "_llm_campaign_strata_version": "v0.1.44.1",
        "_llm_campaign_strata_batch_size": 4,
        "_llm_campaign_concurrency_value": 8,
        "_llm_campaign_started_utc": {"stdout": "2026-10-16T20:00:00Z"},
        "_llm_campaign_workdir": "/tmp/example-workspace",
        "_llm_campaign_cell_slug": "example-row-c25-c8-r1",
        "_llm_campaign_service_command": {"stdout": "strata serve --max-context 65536"},
        "hostvars": {"localhost": {"_llm_campaign_config_ref": "f" * 40}},
    }

    rendered = environment.from_string(TEMPLATE.read_text(encoding="utf-8")).render(**context)
    dimensions = json.loads(rendered)["campaign_dimensions"]

    assert dimensions["software"]["engine"] == "Strata"
    assert dimensions["software"]["engine_version"] == "v0.1.44.1"
    assert dimensions["software"]["backend"] == "CUDA"
    assert dimensions["run"] == {
        "allocated_context_tokens": 65536,
        "batch_size": 4,
        "concurrent_agents": 8,
        "output_tokens": 512,
        "prompt_tokens": 25,
        "repeats": 2,
        "speculative_mtp": True,
    }
