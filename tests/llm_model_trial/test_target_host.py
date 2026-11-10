from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
import yaml

PREFLIGHT = Path(__file__).resolve().parents[2] / "roles/llm_model_trial/tasks/preflight.yml"
GROUPS = {"all": ["gpu-baseline", "gpu-candidate"], "llm_gpu_group": ["gpu-baseline"]}


def _facts() -> dict:
    found: dict = {}

    def walk(items: list) -> None:
        for task in items:
            walk(task.get("block", []))
            found.update(task.get("ansible.builtin.set_fact", {}))

    walk(yaml.safe_load(PREFLIGHT.read_text()))
    return found


def _render(expression: str, target: str) -> str:
    env = jinja2.Environment()
    return env.from_string(expression).render(groups=GROUPS, llm_model_trial_target_group=target).strip()


@pytest.mark.parametrize(
    ("target", "expected"),
    [("llm_gpu_group", "gpu-baseline"), ("gpu-candidate", "gpu-candidate")],
)
def test_candidate_host_resolves_from_a_group_or_one_host(target: str, expected: str) -> None:
    assert _render(_facts()["llm_model_trial_candidate_host"], target) == expected


def test_container_settings_list_one_named_host_and_none_for_unknown_names() -> None:
    expression = _facts()["llm_model_trial_container_settings"]
    hosts = expression.split("'target_hosts':", 1)[1].split("'baseline_hosts'", 1)[0].rstrip().rstrip(",")
    probe = "{{ (" + hosts + ") | join(',') }}"
    assert _render(probe, "gpu-candidate") == "gpu-candidate"
    assert _render(probe, "no-such-host") == ""
    assert _render(probe, "llm_gpu_group") == "gpu-baseline"
