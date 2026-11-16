"""Check the first package install of the role refreshes the package index."""

from __future__ import annotations

from pathlib import Path

import yaml

TASK_FILE = (
    Path(__file__).resolve().parents[2]
    / "roles/hermes_agent/tasks/install_prerequisites.yml"
)


def test_prerequisite_install_refreshes_a_stale_package_index():
    apt = yaml.safe_load(TASK_FILE.read_text(encoding="utf-8"))[0]["ansible.builtin.apt"]
    assert apt["update_cache"] is True
    assert apt["cache_valid_time"] == 3600
