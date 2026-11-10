from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import jinja2
import pytest


ROLE = Path(__file__).resolve().parents[2] / "roles/llm_model_trial"
DIGEST = "registry.example/vllm/vllm-openai@sha256:" + "c" * 64


def render(**overrides: Any) -> str:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters.update(comment=lambda value: "# " + str(value), quote=shlex.quote)
    template = env.from_string((ROLE / "templates/llm-model-trial-container.service.j2").read_text())
    values: dict[str, Any] = {
        "ansible_managed": "managed by test",
        "llm_model_trial_id": "trial123",
        "llm_model_trial_model_dir": "/models/example/model",
        "llm_model_trial_bind_host": "192.0.2.20",
        "llm_model_trial_port": 10435,
        "hf_repo": "example/model",
        "llm_model_trial_container_image": DIGEST,
        "llm_model_trial_container_env": {},
        "llm_model_trial_container_args": [],
        "llm_model_trial_window_minutes": 60,
    }
    values.update(overrides)
    return template.render(**values)


def exec_start(unit: str) -> list[str]:
    lines = unit.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("ExecStart=/usr/bin/docker run"))
    block: list[str] = []
    for line in lines[start:]:
        block.append(line)
        if not line.endswith("\\"):
            break
    return block


def test_container_unit_mounts_only_the_model_directory_read_only() -> None:
    unit = render()
    assert unit.count("--volume ") == 1
    assert "--volume /models/example/model:/model:ro" in unit
    for forbidden in ("--privileged", "--env-file", " -v ", "--mount", "--cap-add", ":rw"):
        assert forbidden not in unit


def test_container_unit_stops_before_the_baseline_restore_timer() -> None:
    assert "RuntimeMaxSec=3480" in render()
    assert "RuntimeMaxSec=1680" in render(llm_model_trial_window_minutes=30)
    assert "RuntimeMaxSec=60" in render(llm_model_trial_window_minutes=3)


def test_container_unit_uses_gpu_access_host_network_and_pinned_image() -> None:
    unit = render()
    block = "\n".join(exec_start(unit))
    assert "--gpus all" in block
    assert "--network host" in block
    assert "--entrypoint vllm" in block
    assert f"  {DIGEST} \\" in block
    assert "--host 192.0.2.20" in block
    assert "--port 10435" in block
    assert "--served-model-name example/model" in block


@pytest.mark.parametrize("env", [{}, {"VLLM_LOG": "INFO"}])
def test_container_unit_has_no_blank_line_inside_exec_start(env: dict[str, str]) -> None:
    # A blank line ends a systemd ExecStart continuation, so the command would be cut short.
    unit = render(llm_model_trial_container_env=env)
    start = unit.index("ExecStart=")
    body = unit[start:unit.index("ExecStop=")]
    lines = body.rstrip("\n").split("\n")
    assert "" not in lines
    assert all(line.endswith("\\") for line in lines[:-1])
    assert not lines[-1].endswith("\\")


def test_container_unit_renders_env_sorted_and_shell_quoted() -> None:
    unit = render(llm_model_trial_container_env={"VLLM_NOTE": "two words", "VLLM_LOG": "INFO"})
    assert "--env 'VLLM_NOTE=two words' \\" in unit
    assert unit.index("--env VLLM_LOG=INFO") < unit.index("--env 'VLLM_NOTE=two words'")


def test_container_unit_appends_args_one_per_continuation_line() -> None:
    unit = render(llm_model_trial_container_args=["--max-model-len", "4096", "--reasoning-parser", "qwen3"])
    block = exec_start(unit)
    assert block[-4:] == [
        "  --max-model-len \\",
        "  4096 \\",
        "  --reasoning-parser \\",
        "  qwen3",
    ]


def test_container_unit_without_args_ends_the_command_cleanly() -> None:
    block = exec_start(render())
    assert block[-1] == "  --served-model-name example/model"
    assert not any(line.endswith("\\") for line in block[-1:])
