"""Keep retired Codex runner cleanup behind the live no-Codex Hermes config."""

from pathlib import Path

import yaml

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TASKS = ROLE / "tasks"


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def test_cleanup_follows_config_render_and_handler_flush() -> None:
    main = yaml.safe_load((TASKS / "main.yml").read_text())
    config = next(
        index for index, task in enumerate(main)
        if task.get("ansible.builtin.include_tasks") == "config_and_content.yml"
    )
    finalize = next(
        index for index, task in enumerate(main)
        if task.get("ansible.builtin.include_tasks") == "finalize.yml"
    )
    cleanup = next(
        index for index, task in enumerate(main)
        if task.get("ansible.builtin.import_tasks") == "decommission_retired_runner.yml"
    )
    finalize_tasks = yaml.safe_load((TASKS / "finalize.yml").read_text())
    finalize_names = [task["name"] for task in finalize_tasks]

    assert config < finalize < cleanup
    assert finalize_names.index("Flush handlers so config/unit changes apply before verification") < (
        finalize_names.index("Verify the brain wiring end-to-end after converge")
    )


def test_cleanup_removes_the_three_exact_retired_artifacts() -> None:
    cleanup = yaml.safe_load((TASKS / "decommission_retired_runner.yml").read_text())
    sudoers = _task(cleanup, "Remove the retired codex-runner sudoers grant")
    user = _task(cleanup, "Remove the retired codex-runner user")
    home = _task(cleanup, "Remove the retired codex-runner home")

    assert sudoers["ansible.builtin.file"] == {
        "path": "/etc/sudoers.d/codex-runner-mcp",
        "state": "absent",
    }
    assert user["ansible.builtin.user"] == {
        "name": "codex-runner",
        "state": "absent",
        "remove": True,
    }
    assert home["ansible.builtin.file"] == {
        "path": "/var/lib/codex-runner",
        "state": "absent",
    }


def test_runner_processes_stop_before_the_account_is_removed() -> None:
    cleanup = yaml.safe_load((TASKS / "decommission_retired_runner.yml").read_text())
    names = [task["name"] for task in cleanup]
    account = _task(cleanup, "Look up the retired codex-runner account")
    stop = _task(cleanup, "Stop processes owned by the retired codex-runner account")
    wait = _task(cleanup, "Wait for retired codex-runner processes to stop")

    assert account["ansible.builtin.getent"] == {
        "database": "passwd",
        "key": "codex-runner",
        "fail_key": False,
    }
    assert stop["ansible.builtin.command"]["argv"] == [
        "pkill", "--signal", "TERM", "--uid", "codex-runner"
    ]
    assert stop["changed_when"] == "hermes_agent_retired_runner_process_stop.rc == 0"
    assert stop["failed_when"] == "hermes_agent_retired_runner_process_stop.rc not in [0, 1]"
    assert wait["until"] == "hermes_agent_retired_runner_processes.rc == 1"
    assert stop["when"] == wait["when"]
    assert "hermes_agent_retired_runner_account_lookup.ansible_facts.getent_passwd" in stop["when"]
    assert names.index(stop["name"]) < names.index(wait["name"]) < names.index(
        "Remove the retired codex-runner user"
    )
