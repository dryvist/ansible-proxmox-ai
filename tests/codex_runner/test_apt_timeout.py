"""The codex_runner package install has a hard time limit."""

from pathlib import Path

import yaml

ROLE = Path(__file__).resolve().parents[2] / "roles/codex_runner"


def test_apt_install_is_bounded_by_the_role_default():
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text(encoding="utf-8"))
    install = next(t for t in tasks if t.get("name") == "Install Node.js/npm (packaged, no NodeSource repo)")
    assert install["timeout"] == "{{ codex_runner_apt_timeout }}"
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text(encoding="utf-8"))
    assert 0 < defaults["codex_runner_apt_timeout"] <= 1800
