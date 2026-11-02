"""The public cheap alias groups both OpenCode Go deployments for sandbox keys."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_public_cheap_route_renders_for_sandbox_only(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "ansible-playbook",
            str(REPO_ROOT / "tests/llm_router/test_public_cheap_route.yml"),
            "-i",
            "localhost,",
            "-c",
            "local",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-local")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
