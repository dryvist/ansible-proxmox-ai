"""Check Hermes component tag structure and the native cron task listing.

The YAML traversal verifies declared tag reachability and untagged static-tree
coverage. It is not a task-count oracle: imported collection roles expand in
Ansible's listing, so the cron ceiling is checked with `ansible-playbook`.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import yaml

from _role_files import include_file

REPO = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO / "playbooks" / "agents.yml"
HERMES_TASKS = REPO / "roles/hermes_agent/tasks"
HERMES_ROLE = REPO / "roles/hermes_agent"
WATCHDOG_ROLE = REPO / "roles/fabric_watchdog"
PLAY_TAGS = ["hermes_agent", "agent", "ai", "llm"]

INCLUDE_KEYS = ("ansible.builtin.import_tasks", "ansible.builtin.include_tasks")
IMPORT_ROLE_KEY = "ansible.builtin.import_role"
INCLUDE_ROLE_KEY = "ansible.builtin.include_role"
# A block container groups children (block/rescue/always) and carries no
# module of its own, so it never appears in a --list-tasks listing.
_CONTAINER_KEYS = {"name", "tags", "when", "vars", "block", "rescue", "always"}
# Reached only through agent-egress.yml's dynamic include_role
# (tasks_from: egress), not from tasks/main.yml.
EGRESS_FILES = {"egress.yml", "egress_domains.yml", "egress_host.yml"}


def _tags(task: dict) -> list[str]:
    tags = task.get("tags", [])
    return [tags] if isinstance(tags, str) else list(tags or [])


def _task_items(tasks: list[dict]) -> list[dict]:
    for task in tasks:
        yield task
        for child_key in ("block", "rescue", "always"):
            children = task.get(child_key, [])
            if isinstance(children, list):
                yield from _task_items(children)


def _import_target(task: dict) -> str | None:
    """The target of a STATIC import, whose contents list in place."""
    target = include_file(task, "ansible.builtin.import_tasks")
    return target if isinstance(target, str) and target.endswith(".yml") else None


def _include_target(task: dict) -> str | None:
    """The target of a DYNAMIC include, which lists as one entry."""
    target = include_file(task, "ansible.builtin.include_tasks")
    return target if isinstance(target, str) and target.endswith(".yml") else None


def _walk(tasks: list[dict], base: Path, inherited: tuple[str, ...],
          source: str) -> list[tuple[str, tuple[str, ...], str]]:
    """Resolve local imports to inspect task names and effective tag paths."""
    listed: list[tuple[str, tuple[str, ...], str]] = []
    for task in tasks:
        effective = tuple(sorted(set(inherited) | set(_tags(task))))
        static_target = _import_target(task)
        dynamic_target = _include_target(task)
        if static_target is not None:
            listed.extend(_walk(yaml.safe_load((base / static_target).read_text()) or [],
                                base, effective, static_target))
            continue
        if dynamic_target is not None:
            listed.append((task.get("name", dynamic_target), effective, source))
            continue
        if IMPORT_ROLE_KEY in task:
            role = task[IMPORT_ROLE_KEY]
            tasks_from = (role.get("tasks_from") or "main") + ".yml"
            if role["name"] != "hermes_agent":
                # Shared collection roles (hermes_runtime, bao_approle_cli):
                # their contents resolve from the installed collection, so
                # they are modeled as one opaque entry inheriting the import
                # tags — exactly how the selection treats them.
                listed.append((task.get("name", tasks_from), effective, source))
                continue
            listed.extend(_walk(yaml.safe_load((HERMES_TASKS / tasks_from).read_text()) or [],
                                HERMES_TASKS, effective, tasks_from))
            continue
        if INCLUDE_ROLE_KEY in task:
            role = task[INCLUDE_ROLE_KEY]
            tasks_from = (role.get("tasks_from") or "main") + ".yml"
            listed.append((task.get("name", tasks_from), effective, source))
            continue
        child_lists = [task.get(k) for k in ("block", "rescue", "always")]
        child_lists = [children for children in child_lists if isinstance(children, list)]
        if child_lists:
            # block/rescue/always carry no module of their own; the children
            # of ALL of them list (a block's rescue runs on its failure).
            for children in child_lists:
                listed.extend(_walk(children, base, effective, source))
            continue
        listed.append((task.get("name", "?"), effective, source))
    return listed


def _play_listing() -> list[tuple[str, tuple[str, ...], str]]:
    play = yaml.safe_load(PLAYBOOK.read_text())[0]
    listed: list[tuple[str, tuple[str, ...], str]] = []
    for section in ("pre_tasks", "tasks"):
        listed.extend(_walk(play.get(section) or [], REPO / "playbooks", tuple(PLAY_TAGS),
                            "play"))
    return listed


def _selected(listing, tag: str | None) -> list[str]:
    if tag is None:
        return [name for name, _, _ in listing]
    return [name for name, tags, _ in listing if "always" in tags or tag in tags]


def test_component_tag_selects_only_its_own_cron_tasks() -> None:
    listing = _selected(_play_listing(), "hermes_crons")
    # The cron reconcile work is present: its own tasks and the tasks of the
    # files statically imported beside it in the same component.
    for task_name in (
        "Disable SSH password authentication",
        "Assert the configured web search backend resolves as available",
        "Reconcile the agentic direct-deliver digest crons",
        "Pause retired Hermes direct crons",
    ):
        assert task_name in listing, task_name
    # Other components stay out: no install-phase wiring asserts, no patches,
    # no config deployment, no post-converge verification.
    for other in (
        "Assert Hermes Agent brain-wiring contracts hold before converging",
        "Patch Hermes cron scheduler with an opt-in goal-mode runner",
        "Deploy secrets, config.yaml, persona content, and Hindsight provider config",
        "Verify the brain wiring end-to-end after converge",
    ):
        assert other not in listing, other
    # The watchdog is a different component on the same guest.
    assert "Include fabric_watchdog role" not in listing


def test_hermes_crons_native_task_listing_stays_below_ceiling(tmp_path: Path) -> None:
    env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-local"))
    listed = subprocess.run(
        [
            "ansible-playbook",
            str(PLAYBOOK),
            "-i",
            "localhost,",
            "--list-tasks",
            "--tags",
            "hermes_crons",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr

    task_names = [
        line.partition("\tTAGS:")[0].strip()
        for line in listed.stdout.splitlines()
        if line.startswith("      ") and "\tTAGS:" in line
    ]
    assert 0 < len(task_names) < 150
    for reconcile_task in (
        "Reconcile the agentic direct-deliver digest crons",
        "Reconcile the script-fed Splunk status-digest cron",
    ):
        assert any(reconcile_task in task for task in task_names), reconcile_task


def test_untagged_run_lists_the_whole_static_tree() -> None:
    # Narrowing must never cost an untagged run anything: without --tags the
    # selection covers every real task in the static import tree. The tree is
    # re-read file-by-file here, not through _walk.
    untagged = set(_selected(_play_listing(), None))
    static_files: set[str] = set()
    pending = ["main.yml"]
    while pending:
        filename = pending.pop()
        if filename in static_files:
            continue
        static_files.add(filename)
        for task in _task_items(yaml.safe_load((HERMES_TASKS / filename).read_text()) or []):
            target = include_file(task, "ansible.builtin.import_tasks")
            if isinstance(target, str) and target.endswith(".yml"):
                pending.append(target)
    container_keys = {"block", "rescue", "always"}
    for filename in static_files - {"main.yml"}:
        for task in _task_items(yaml.safe_load((HERMES_TASKS / filename).read_text()) or []):
            if include_file(task, "ansible.builtin.import_tasks") or include_file(
                task, "ansible.builtin.include_tasks"
            ):
                # Import/include entries are transparent in the listing; what
                # must survive is every task of their target files.
                continue
            if container_keys & set(task):
                # Block containers only group their children.
                continue
            assert task["name"] in untagged, (filename, task["name"])
    # test_entry_point_tags.py separately walks the import tree and fails
    # when any task file stops being reachable from main.yml.


def test_watchdog_tag_reaches_the_watchdog() -> None:
    # apply carries the tag into the dynamic contents (the include entry
    # alone would leave the watchdog's tasks untagged and skipped).
    play = yaml.safe_load(PLAYBOOK.read_text())[0]
    block = play["tasks"][0]["block"]
    include = next(t for t in block if t.get(INCLUDE_ROLE_KEY))
    assert include[INCLUDE_ROLE_KEY]["apply"]["tags"] == ["fabric_watchdog"]
    assert "fabric_watchdog" in _tags(include)
    watchdog = yaml.safe_load((WATCHDOG_ROLE / "tasks" / "main.yml").read_text())
    assert watchdog, "fabric_watchdog lost its tasks"
