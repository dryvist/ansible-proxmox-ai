"""The uv installer runs only when the pinned version is not already reported."""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED = "0.12.21"


@pytest.mark.parametrize(
    ("tasks_file", "prefix"),
    [
        (REPO_ROOT / "roles/nvidia_gpu_guest/tasks/main.yml", "nvidia_gpu_guest"),
        (REPO_ROOT / "roles/llm_gpu_serving/tasks/main.yml", "llm_gpu_serving"),
    ],
)
def test_uv_installer_is_skipped_only_when_the_pinned_version_is_reported(tasks_file: Path, prefix: str) -> None:
    tasks = yaml.safe_load(tasks_file.read_text(encoding="utf-8"))
    installer = next(task for task in tasks if task.get("name") == "Install the pinned uv version")
    template = jinja2.Environment().from_string("{{ " + installer["when"].replace("\n", " ") + " }}")

    def decide(stdout: str) -> str:
        return template.render(**{f"{prefix}_uv_version_check": {"stdout": stdout}, f"{prefix}_uv_version": PINNED})

    assert decide("uv 0.12.21") == "False"
    assert decide("uv 0.12.21 (aarch64-apple-darwin)") == "False"
    assert decide("uv 0.11.21 (aarch64-apple-darwin)") == "True"
    assert decide("") == "True"
