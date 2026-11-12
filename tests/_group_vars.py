from __future__ import annotations

from pathlib import Path

import jinja2
import jinja2.nativetypes
import yaml

GROUP_VARS = Path(__file__).resolve().parents[1] / "inventory/group_vars"
GROUP_VARS_ALL = GROUP_VARS / "all"


def load_group_vars_all(group_vars_dir: Path | None = None) -> dict:
    directory = group_vars_dir if group_vars_dir is not None else GROUP_VARS_ALL
    return {
        key: value
        for path in sorted(directory.glob("*.yml"))
        for key, value in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items()
    }


def group_var(name: str, group: str | None = None):
    """A declared value as Ansible resolves it for a host in `group` (all when None).

    A value that references another variable is rendered against the merged
    declarations, so a guest label resolves through the lane label it names.
    """
    values = load_group_vars_all()
    if group is not None:
        values.update(yaml.safe_load((GROUP_VARS / f"{group}.yml").read_text(encoding="utf-8")) or {})
    value = values[name]
    if isinstance(value, str) and "{{" in value:
        env = jinja2.nativetypes.NativeEnvironment(undefined=jinja2.StrictUndefined)
        return env.from_string(value).render(**values)
    return value
