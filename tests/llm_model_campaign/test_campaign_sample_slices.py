"""Campaign sample slices expand into unique, recipe-sized cells."""

from __future__ import annotations

import ast
import json
import re
from itertools import product
from pathlib import Path
from typing import Any

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = REPO_ROOT / "playbooks"
TASKS = PLAYBOOKS / "tasks"
CELL_TASKS = TASKS / "llm-model-campaign-cell.yml"
ARGV_TEMPLATE = PLAYBOOKS / "templates/llm-model-campaign-argv.json.j2"


def _load(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _named(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def _jinja() -> jinja2.Environment:
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
    environment.filters["bool"] = bool
    environment.filters["to_json"] = json.dumps
    environment.filters["from_json"] = json.loads
    return environment


def test_sample_slice_values_select_the_recipe_chunk_and_render_as_json() -> None:
    tasks = _load(CELL_TASKS)
    cell_parameters = _named(tasks, "Build the config-declared arguments from the current row parameters")
    slice_expression = cell_parameters["ansible.builtin.set_fact"]["sample_slice"]
    selected = ast.literal_eval(
        _jinja()
        .from_string(slice_expression)
        .render(
            _llm_campaign_run={
                "sample_task": "leaderboard_mmlu_pro",
                "sample_indices": [9, 22, 29, 102, 190],
                "slice_size": 2,
                "dimensions": ["sample_slice_list"],
            },
            _llm_campaign_sample_slice_value=1,
        )
    )
    assert selected == {"leaderboard_mmlu_pro": [29, 102]}

    rendered = (
        _jinja()
        .from_string(ARGV_TEMPLATE.read_text(encoding="utf-8"))
        .render(
            _llm_campaign_run={"argv": ["--samples", "{{ sample_slice }}"]},
            artifact_path="/models/example",
            benchmark_endpoint_root="https://example.invalid",
            benchmark_endpoint="https://example.invalid/v1",
            model_id="example/model",
            context_window_tokens="32768",
            context="32768",
            concurrency="1",
            repetition="1",
            sample_slice=selected,
            output_tokens="4096",
            result_dir="/results/cell",
            result_filename="cell.json",
            result_path="/results/cell/cell.json",
            output_path="/results/cell",
        )
    )
    assert json.loads(rendered) == ["--samples", json.dumps(selected)]


def test_sample_slice_index_is_part_of_the_cell_result_path() -> None:
    tasks = _load(CELL_TASKS)
    allocation = _named(tasks, "Allocate a unique recipe cell name and result paths")
    expression = allocation["ansible.builtin.set_fact"]["_llm_campaign_cell_slug"]
    slug_template = _jinja().from_string(expression)
    base = {
        "_llm_campaign_run": {"name": "mmlu-pro-quick", "dimensions": ["sample_slice_list"]},
        "_llm_campaign_paired_trial": False,
        "llm_model_campaign_pass_name": "baseline",
        "_llm_campaign_context_value": 32768,
        "_llm_campaign_concurrency_value": 1,
        "_llm_campaign_repetition_value": 1,
    }

    slugs = [
        slug_template.render(**base, _llm_campaign_sample_slice_value=index)
        for index in (0, 1)
    ]

    assert slugs == ["mmlu-pro-quick-s0-r1", "mmlu-pro-quick-s1-r1"]
    assert slugs[0] != slugs[1]


def test_cell_loop_uses_the_cartesian_product_of_all_campaign_axes() -> None:
    run_tasks = _load(TASKS / "llm-model-campaign-run.yml")
    axes = {
        **_named(run_tasks, "Count the sample slices for this recipe row")["ansible.builtin.set_fact"],
        **_named(run_tasks, "Resolve the surveyed axes for this recipe row")["ansible.builtin.set_fact"],
    }
    loop = _named(
        run_tasks,
        "Run the row for each requested context, concurrency, sample slice, and repetition",
    )["loop"]
    environment = _jinja()
    environment.globals["query"] = lambda _plugin, *axes: list(product(*axes))
    expression = environment.from_string(loop)
    run = {
        "name": "mmlu-pro-quick",
        "dimensions": ["context_list", "concurrency_list", "sample_slice_list"],
        "sample_task": "leaderboard_mmlu_pro",
        "sample_indices": list(range(420)),
        "slice_size": 30,
        "repetitions": 2,
    }

    def render_fact(name: str, **overrides: Any) -> Any:
        slice_count = ast.literal_eval(
            environment.from_string(axes["_llm_campaign_sample_slice_count"]).render(
                _llm_campaign_run=run
            )
        )
        context = {
            "_llm_campaign_run": run,
            "_llm_campaign_contexts": [32768, 65536, 131072],
            "_llm_campaign_concurrencies": [1, 2, 4, 8],
            "_llm_campaign_sample_slice_count": slice_count,
            **overrides,
        }
        return ast.literal_eval(environment.from_string(axes[name]).render(**context))

    cells = ast.literal_eval(
        expression.render(
            _llm_campaign_context_axis=render_fact("_llm_campaign_context_axis"),
            _llm_campaign_concurrency_axis=render_fact("_llm_campaign_concurrency_axis"),
            _llm_campaign_sample_slice_axis=render_fact("_llm_campaign_sample_slice_axis"),
            _llm_campaign_repetition_count=render_fact("_llm_campaign_repetition_count"),
        )
    )

    assert len(cells) == 14 * 2 * 3 * 4

    one_slice = render_fact("_llm_campaign_sample_slice_axis", llm_model_campaign_sample_slice_index=5)
    one_repetition = render_fact("_llm_campaign_repetition_count", llm_model_campaign_repetitions=1)
    one_cell = ast.literal_eval(
        expression.render(
            _llm_campaign_context_axis=[32768],
            _llm_campaign_concurrency_axis=[1],
            _llm_campaign_sample_slice_axis=one_slice,
            _llm_campaign_repetition_count=one_repetition,
        )
    )
    assert one_slice == [5]
    assert one_repetition == 1
    assert len(one_cell) == 1
