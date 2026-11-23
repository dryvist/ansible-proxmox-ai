"""A model download tolerates only the GPU processes its caller names."""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
import yaml
from ansible.plugins.filter.core import FilterModule

REPO_ROOT = Path(__file__).resolve().parents[2]
LLAMA_SERVER_LINES = [
    "153188, /opt/llama-cpp/llama-server, 6381 MiB",
    "153251, /opt/llama-cpp/llama-server, 2641 MiB",
]


def _guard() -> jinja2.Template:
    tasks = yaml.safe_load((REPO_ROOT / "roles/llm_gpu_serving/tasks/cache-sync.yml").read_text(encoding="utf-8"))
    task = next(item for item in tasks if item.get("name") == "Require an idle GPU before downloading a registered artifact")
    env = jinja2.Environment()
    env.filters.update(FilterModule().filters())
    return env.from_string("{{ " + task["ansible.builtin.assert"]["that"][0] + " }}")


@pytest.mark.parametrize(
    ("lines", "tolerated", "allowed"),
    [
        ([], [], "True"),
        (LLAMA_SERVER_LINES, [], "False"),
        (LLAMA_SERVER_LINES, ["llama-server"], "True"),
        ([*LLAMA_SERVER_LINES, "9001, /usr/bin/python3, 1200 MiB"], ["llama-server"], "False"),
    ],
)
def test_download_guard_tolerates_only_named_processes(lines: list[str], tolerated: list[str], allowed: str) -> None:
    rendered = _guard().render(
        llm_gpu_serving_cache_sync_compute_apps={"stdout_lines": lines},
        llm_gpu_serving_cache_sync_tolerated_compute_apps=tolerated,
    )

    assert rendered == allowed


def test_role_default_keeps_the_gpu_idle_and_the_llama_cpp_play_tolerates_its_server() -> None:
    core = yaml.safe_load((REPO_ROOT / "roles/llm_gpu_serving/defaults/main/00-core.yml").read_text(encoding="utf-8"))
    plays = yaml.safe_load((REPO_ROOT / "playbooks/llm-serving-llama-cpp.yml").read_text(encoding="utf-8"))
    play = next(item for item in plays if item["hosts"] == "llm_vllm_group")
    block = next(item for item in play["tasks"] if "co-resident" in item["name"])["block"]
    include = next(step for step in block if "ansible.builtin.include_role" in step)

    assert core["llm_gpu_serving_cache_sync_tolerated_compute_apps"] == []
    assert include["vars"]["llm_gpu_serving_cache_sync_tolerated_compute_apps"] == ["llama-server"]
