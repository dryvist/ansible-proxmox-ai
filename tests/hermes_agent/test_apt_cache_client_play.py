"""The Hermes play points apt at the internal caches before any package task."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = "dryvist.homelab.apt_cache_client"


def test_hermes_play_applies_the_apt_cache_client_first():
    plays = yaml.safe_load((ROOT / "playbooks/agents.yml").read_text(encoding="utf-8"))
    play = next(play for play in plays if play.get("hosts") == "hermes_agent_group")
    first = play["pre_tasks"][0]

    assert first["ansible.builtin.import_role"]["name"] == ROLE
    # Its own tag for a scoped run, and the install component's tag, whose run installs packages.
    assert {"apt_cache_client", "hermes_runtime_install"} <= set(first["tags"])
