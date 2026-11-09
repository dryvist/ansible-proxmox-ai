"""Run the native Compose render contracts in the role's CI pytest selection."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "playbook",
    [
        "tests/dify_docker/test_compose_ssr_api_url.yml",
        "tests/dify_docker/test_ssrf_trigger_carveout.yml",
    ],
)
def test_compose_render_contract(playbook):
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        ["ansible-playbook", playbook, "-i", "localhost,", "-c", "local"],
        cwd=root,
        check=True,
    )
