"""The enqueuer reuses batched cron and marker snapshots without file probes."""
from pathlib import Path

import yaml
from jinja2 import Environment

TASKS = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent" / "tasks"
CHECKSUM = "a" * 64
ENV = Environment()
ENV.filters["bool"] = lambda value: (
    value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "1")
)


def _load(name: str) -> list[dict]:
    return yaml.safe_load((TASKS / name).read_text())


def _context(*, listed: bool, marker: str | None, size: int | None) -> dict:
    return {
        "item": {"name": "job-a"},
        "hermes_agent_enqueuer_active": True,
        "hermes_agent_enqueuer_name": "job-a",
        "hermes_agent_enqueuer_checksum": CHECKSUM,
        "hermes_agent_enqueuer_cron_list": {"stdout": "job-a" if listed else ""},
        "hermes_agent_direct_markers": {"job-a": marker} if marker is not None else {},
        "hermes_agent_direct_marker_sizes": {"job-a": size} if size is not None else {},
        "hermes_agent_id": "hermes",
        "hermes_agent_job_identities": ["hermes"],
        "ansible_check_mode": False,
    }


def _marker_changed(context: dict) -> bool:
    probe = next(
        task
        for task in _load("reconcile_enqueuer_cron.yml")
        if "Check desired-state marker" in task["name"]
    )
    marker_probe = probe["ansible.builtin.set_fact"][
        "hermes_agent_enqueuer_marker_probe"
    ]
    expression = marker_probe["changed"]
    return ENV.from_string(expression).render(**context) == "True"


def _commands(*, listed: bool, marker: str | None, size: int | None) -> set[str]:
    context = _context(listed=listed, marker=marker, size=size)
    context["hermes_agent_enqueuer_marker_probe"] = {
        "changed": _marker_changed(context)
    }
    ran = set()
    for task in _load("reconcile_enqueuer_cron.yml"):
        conditions = task.get("when", [])
        conditions = [conditions] if isinstance(conditions, str) else conditions
        if "ansible.builtin.command" in task and all(
            ENV.compile_expression(str(condition))(**context)
            for condition in conditions
        ):
            ran.add(task["name"].split(" {{")[0])
    return ran


def test_enqueuer_reuses_cron_and_marker_snapshots():
    tasks = _load("reconcile_enqueuer_cron.yml")
    assert not any("cron list --all" in str(task) for task in tasks)
    probe = next(task for task in tasks if "Check desired-state marker" in task["name"])
    assert "ansible.builtin.copy" not in probe and "check_mode" not in probe
    assert "hermes_agent_direct_markers" in str(probe)
    assert "hermes_agent_direct_marker_sizes" in str(probe)

    batch = _load("cron_reconcile_direct_batch.yml")[0]["block"]
    assert sum("ansible.builtin.find" in task for task in batch) == 1


def test_marker_comparison_matches_copy_content_and_treats_absence_as_drift():
    marker_size = len(CHECKSUM) + 1
    context = _context(listed=True, marker=CHECKSUM, size=marker_size)
    assert _marker_changed(context) is False
    assert _marker_changed(_context(listed=True, marker=CHECKSUM, size=64)) is True
    assert _marker_changed(_context(listed=True, marker="old", size=4)) is True
    assert _marker_changed(_context(listed=True, marker=None, size=None)) is True


def test_unchanged_job_is_skipped():
    assert _commands(listed=True, marker=CHECKSUM, size=65) == set()


def test_drifted_job_is_removed_and_recreated():
    assert _commands(listed=True, marker="old", size=4) == {
        "Enqueuer cron | Remove drifted",
        "Enqueuer cron | Create",
    }


def test_missing_job_is_created():
    assert _commands(listed=False, marker=None, size=None) == {"Enqueuer cron | Create"}
