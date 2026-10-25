from __future__ import annotations

import configparser
import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LANES_RELATIVE_PATH = Path(
    "ansible_collections/dryvist/homelab/roles/llm_roles/files/lanes.json"
)
INSTALL_HINT = "Run `ansible-galaxy install -r requirements.yml`."


def _collections_paths() -> list[Path]:
    configured_paths = os.environ.get("ANSIBLE_COLLECTIONS_PATH") or os.environ.get(
        "ANSIBLE_COLLECTIONS_PATHS"
    )
    if configured_paths is None:
        config = configparser.ConfigParser()
        config.read(REPO_ROOT / "ansible.cfg")
        configured_paths = config.get("defaults", "collections_path", fallback="")
    paths = [Path(path).expanduser() for path in configured_paths.split(os.pathsep) if path]
    if paths:
        return paths
    return [Path.home() / ".ansible/collections", Path("/usr/share/ansible/collections")]


@pytest.fixture(scope="session")
def llm_lane_contract() -> dict[str, list[str]]:
    candidates = [root / LANES_RELATIVE_PATH for root in _collections_paths()]
    lanes_path = next((path for path in candidates if path.is_file()), None)
    if lanes_path is None:
        searched = ", ".join(str(path) for path in candidates)
        pytest.fail(
            f"Could not find homelab-contracts lanes.json at {searched}. {INSTALL_HINT}",
            pytrace=False,
        )

    try:
        lanes = json.loads(lanes_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        pytest.fail(
            f"Could not read homelab-contracts lanes.json at {lanes_path}: {error}. {INSTALL_HINT}",
            pytrace=False,
        )
    if not isinstance(lanes, dict) or any(
        not isinstance(lanes.get(key), list)
        or not all(isinstance(name, str) for name in lanes[key])
        for key in ("vocabulary", "pending")
    ):
        pytest.fail(
            f"Invalid homelab-contracts lanes.json at {lanes_path}: "
            f"expected string lists for vocabulary and pending. {INSTALL_HINT}",
            pytrace=False,
        )
    return lanes
