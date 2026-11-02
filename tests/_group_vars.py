from __future__ import annotations

from pathlib import Path

import yaml

GROUP_VARS_ALL = Path(__file__).resolve().parents[1] / "inventory/group_vars/all"


def load_group_vars_all(group_vars_dir: Path | None = None) -> dict:
    directory = group_vars_dir if group_vars_dir is not None else GROUP_VARS_ALL
    return {
        key: value
        for path in sorted(directory.glob("*.yml"))
        for key, value in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items()
    }
