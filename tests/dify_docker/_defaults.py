"""Load Dify role defaults in the same filename order as Ansible."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main"
DEFAULTS_FILES = tuple(sorted(DEFAULTS.glob("*.yml")))


def load_defaults():
    defaults = {}
    for path in DEFAULTS_FILES:
        defaults.update(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    return defaults


def load_defaults_text():
    return "\n".join(path.read_text(encoding="utf-8") for path in DEFAULTS_FILES)
