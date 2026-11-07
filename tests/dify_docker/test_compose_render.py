"""Run the native Compose render contract in the role's CI pytest selection."""

import subprocess
from pathlib import Path


def test_compose_render_contract():
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [
            "ansible-playbook",
            "tests/dify_docker/test_compose_ssr_api_url.yml",
            "-i", "localhost,", "-c", "local",
        ],
        cwd=root,
        check=True,
    )
