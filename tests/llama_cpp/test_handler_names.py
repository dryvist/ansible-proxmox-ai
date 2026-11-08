"""Every notify in the llama_cpp role must address a notifiable handler.

Ansible registers handler names for TOP-LEVEL handler tasks only. A handler
written as a named block is not addressable by `notify` — the play fails at the
first notifying task with "The requested handler ... was not found in either
the main handlers list nor in the listening handlers list". The addressable
form for a shared handler name is `listen:` on top-level tasks (the pattern
roles/hermes_agent and roles/llm_router already use).

These assertions pin the wiring, not the wording: a notify value no top-level
handler name or listen group provides fails here instead of at the first
converge that reports a change, and a block can never reappear in a handlers
file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_SUFFIXES = (".yml", ".yaml")


def _yaml_files(root: Path, pattern: str) -> list[Path]:
    """Every YAML file matching pattern under root, both suffixes."""
    return sorted(p for p in root.glob(pattern) if p.suffix in YAML_SUFFIXES)


TASK_FILES = _yaml_files(REPO_ROOT / "roles" / "llama_cpp" / "tasks", "**/*")
HANDLER_FILES = _yaml_files(REPO_ROOT / "roles", "*/handlers/**/*")

# block/rescue/always wrap task lists at any depth, and notifies sit inside
# them too (install-release.yml notifies from inside a nested block).
WRAPPER_KEYS = ("block", "rescue", "always")


def _iter_tasks(entries):
    """Yield every task dict under a parsed task list, depth-first."""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        yield entry
        for key in WRAPPER_KEYS:
            nested = entry.get(key)
            if isinstance(nested, list):
                yield from _iter_tasks(nested)


def _as_list(value):
    """A scalar-or-list YAML value as a list, skipping an absent one."""
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _notifies() -> set[str]:
    """Every notify value in any YAML file under the llama_cpp role's tasks/."""
    values: set[str] = set()
    for path in TASK_FILES:
        tasks = yaml.safe_load(path.read_text()) or []
        for task in _iter_tasks(tasks):
            values.update(_as_list(task.get("notify")))
    return values


def _handler_identifiers() -> set[str]:
    """Top-level handler names plus listen groups in llama_cpp's handlers."""
    identifiers: set[str] = set()
    for path in _yaml_files(REPO_ROOT / "roles" / "llama_cpp" / "handlers", "**/*"):
        for handler in yaml.safe_load(path.read_text()) or []:
            if not isinstance(handler, dict):
                continue
            identifiers.update(_as_list(handler.get("name")))
            identifiers.update(_as_list(handler.get("listen")))
    return identifiers


def test_every_notify_resolves_to_a_notifiable_handler() -> None:
    """Each notify value must name a top-level handler name or listen group."""
    notifies = _notifies()
    assert notifies, "no notify values found under tasks/; the walk broke"
    unaddressed = sorted(notifies - _handler_identifiers())
    assert not unaddressed, (
        f"notify values {unaddressed} name no top-level handler name or listen group"
    )


def test_no_handler_file_defines_a_block() -> None:
    """A block's name is never registered as a handler name, so a handler
    written as a block hangs a name that notify can never reach."""
    for path in HANDLER_FILES:
        for i, handler in enumerate(yaml.safe_load(path.read_text()) or []):
            assert not (isinstance(handler, dict) and "block" in handler), (
                f"{path.relative_to(REPO_ROOT)} entry {i} ({handler.get('name')}) "
                "is a block; blocks are not notifiable handlers — use listen: "
                "on top-level handler tasks"
            )
