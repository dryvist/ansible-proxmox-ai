"""Run the real lifecycle playbook with harmless local role fixtures."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = yaml.safe_load((ROOT / "roles/hindsight_docker/defaults/main.yml").read_text())
IMAGE = DEFAULTS["hindsight_docker_image"]


@pytest.fixture
def lifecycle(tmp_path: Path):
    roles = tmp_path / "roles"
    backend = roles / "hindsight_docker"
    dr = roles / "hindsight_bank_dr"
    router = roles / "llm_router"
    for role in (backend, dr, router):
        (role / "tasks").mkdir(parents=True)
        (role / "defaults").mkdir()
    (backend / "defaults/main.yml").write_text(yaml.safe_dump({
        "hindsight_docker_image": IMAGE,
        "hindsight_docker_migration_approved_image": "",
    }))
    preflight = ROOT / "roles/hindsight_docker/tasks/maintenance_preflight.yml"
    (backend / "tasks/maintenance_preflight.yml").write_text(yaml.safe_dump([
        {"ansible.builtin.import_tasks": str(preflight)},
    ]))
    (backend / "tasks/contain.yml").write_text(
        "- ansible.builtin.debug: {msg: BACKEND_STOP}\n"
        "- ansible.builtin.assert:\n    that: not (fixture_backend_failure | default(false) | bool)\n"
    )
    (backend / "tasks/main.yml").write_text("- ansible.builtin.debug: {msg: BACKEND_MAIN}\n")
    (router / "tasks/registry.yml").write_text("- ansible.builtin.debug: {msg: REGISTRY}\n")
    (dr / "tasks/stop.yml").write_text(
        "- ansible.builtin.assert:\n    that: not (fixture_dr_failure | default(false) | bool)\n"
    )
    (dr / "tasks/main.yml").write_text("- ansible.builtin.debug: {msg: DR_MAIN}\n")
    playbook = tmp_path / "play.yml"
    site = yaml.safe_load((ROOT / "playbooks/site.yml").read_text())
    final_gate = next(play for play in site
                      if play.get("name") == "Fail the run if any isolated play recorded a failure")
    caller_message = ("CALLER_REACHED {{ hostvars['h1'].site_isolated_failures | default([]) }} "
                      "{{ hostvars['h2'].site_isolated_failures | default([]) }}")
    playbook.write_text(yaml.safe_dump([
        {"import_playbook": str(ROOT / "playbooks/hindsight.yml")},
        {"hosts": "localhost", "gather_facts": False, "tags": ["hindsight_bank_dr", "hindsight_docker"],
         "tasks": [{"ansible.builtin.debug": {"msg": caller_message}}]},
        final_gate,
    ]))
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(yaml.safe_dump({"all": {"children": {"hindsight_group": {"hosts": {
        "h1": {"ansible_connection": "local", "ansible_become": False},
        "h2": {"ansible_connection": "local", "ansible_become": False},
    }}}}}))
    env = {**os.environ, "ANSIBLE_ROLES_PATH": str(roles), "ANSIBLE_LOCAL_TEMP": str(tmp_path / "local"),
           "ANSIBLE_HOST_KEY_CHECKING": "False", "ANSIBLE_NOCOLOR": "1"}

    def run(*args: str):
        return subprocess.run(["ansible-playbook", "-i", str(inventory), str(playbook), *args],
                              env=env, text=True, capture_output=True, check=False)
    return run, inventory


def test_dr_tag_does_not_stop_backend_for_approved_enabled_service(lifecycle):
    run, _ = lifecycle
    result = run("--tags", "hindsight_bank_dr", "-e",
                 f"hindsight_service_enabled=true hindsight_docker_migration_approved_image={IMAGE}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BACKEND_STOP" not in result.stdout
    assert "CALLER_REACHED" in result.stdout


@pytest.mark.parametrize("extra", ["fixture_dr_failure=true", "hindsight_service_enabled=false"])
def test_ordinary_failure_or_unreachable_does_not_block_later_caller(lifecycle, extra):
    run, inventory = lifecycle
    if extra == "hindsight_service_enabled=false":
        data = yaml.safe_load(inventory.read_text())
        data["all"]["children"]["hindsight_group"]["hosts"]["h2"] = {
            "ansible_connection": "ssh", "ansible_become": False, "ansible_host": "127.0.0.1", "ansible_port": 1,
            "ansible_ssh_common_args": "-o ConnectTimeout=1",
        }
        inventory.write_text(yaml.safe_dump(data))
    result = run("--tags", "hindsight_bank_dr", "-e", extra)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "CALLER_REACHED" in result.stdout
    assert "isolated play failure(s)" in result.stdout
    assert "hindsight_bank_dr_containment" in result.stdout or "replica unreachable" in result.stdout


@pytest.mark.parametrize("case", ["backend_failure", "all_unreachable"])
def test_backend_containment_continues_to_callers(lifecycle, case):
    run, inventory = lifecycle
    if case == "all_unreachable":
        data = yaml.safe_load(inventory.read_text())
        for host in data["all"]["children"]["hindsight_group"]["hosts"]:
            data["all"]["children"]["hindsight_group"]["hosts"][host] = {
                "ansible_connection": "ssh", "ansible_become": False, "ansible_host": "127.0.0.1",
                "ansible_port": 1, "ansible_ssh_common_args": "-o ConnectTimeout=1",
            }
        inventory.write_text(yaml.safe_dump(data))
    result = run("--tags", "hindsight_docker", "-e", "fixture_backend_failure=true")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "CALLER_REACHED" in result.stdout
    assert "isolated play failure(s)" in result.stdout
    assert "hindsight_docker_containment" in result.stdout or "replica unreachable" in result.stdout


@pytest.mark.parametrize("target,limit", [("wrong", "h1,h2"), (IMAGE, "h1")])
def test_maintenance_rejects_wrong_target_or_partial_fleet_before_stop(lifecycle, target, limit):
    run, _ = lifecycle
    result = run("--tags", "hindsight_docker", "--limit", limit,
                 "-e", f"hindsight_docker_migration_approved_image={target}")
    assert result.returncode != 0
    assert "Approved maintenance requires the exact immutable target" in result.stdout
    assert "BACKEND_STOP" not in result.stdout
    assert "Stop DR timers and active jobs" not in result.stdout
