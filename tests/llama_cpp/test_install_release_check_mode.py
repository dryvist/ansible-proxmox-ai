"""The llama.cpp release install previews in check mode and never touches the guest."""

from __future__ import annotations

import subprocess
from pathlib import Path

PLAYBOOK = Path(__file__).with_name("_install_release_check_mode.yml")


def test_install_release_check_mode_playbook_passes() -> None:
    result = subprocess.run(
        ["ansible-playbook", str(PLAYBOOK), "-i", "localhost,", "-c", "local"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
