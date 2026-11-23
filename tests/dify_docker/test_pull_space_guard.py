"""The Dify deploy refuses to pull without room, and pulls before recreating anything."""

from pathlib import Path

import yaml

from _defaults import load_defaults


ROOT = Path(__file__).resolve().parents[2]
CONFIG_TASKS = ROOT / "roles/dify_docker/tasks/config_and_deploy.yml"
ROOT_READ_TASK = "Read Docker's data root directory"
MOUNTS_TASK = "Resolve free bytes on the filesystem holding Docker's data root"
DF_READ_TASK = "Read free bytes on the Docker data filesystem when mount facts are absent"
ASSERT_TASK = "Require room for a full second Dify image set on the Docker data filesystem"
PULL_TASK = "Pull Dify images before any container is recreated"
DEPLOY_TASK = "Deploy Dify via docker compose"


def _config_tasks():
    return yaml.safe_load(CONFIG_TASKS.read_text(encoding="utf-8"))


def test_free_space_assert_precedes_the_compose_deploy():
    tasks = _config_tasks()
    names = [task.get("name") for task in tasks]
    assert names.index(ASSERT_TASK) < names.index(DEPLOY_TASK)

    assertion = tasks[names.index(ASSERT_TASK)]["ansible.builtin.assert"]
    that = str(assertion["that"])
    assert "dify_docker_docker_root_free_bytes" in that
    assert "dify_docker_min_free_bytes_before_pull" in that

    fail_msg = str(assertion["fail_msg"])
    assert "dify_docker_docker_root_free_bytes" in fail_msg
    assert "dify_docker_min_free_bytes_before_pull" in fail_msg
    assert "grow the guest disk" in fail_msg.lower()
    # Out-of-space guidance must never suggest dropping Dify state to make room.
    for suggestion in ("delete", "remove", "prune"):
        assert suggestion not in fail_msg.lower()


def test_explicit_pull_precedes_the_compose_deploy():
    tasks = _config_tasks()
    names = [task.get("name") for task in tasks]
    assert names.index(PULL_TASK) < names.index(DEPLOY_TASK)

    pull = tasks[names.index(PULL_TASK)]["community.docker.docker_compose_v2_pull"]
    assert pull["project_src"] == "{{ dify_docker_data_dir }}"

    deploy = tasks[names.index(DEPLOY_TASK)]["community.docker.docker_compose_v2"]
    assert deploy["state"] == "present"


def test_free_bytes_resolution_reads_docker_root_before_the_assert():
    tasks = _config_tasks()
    names = [task.get("name") for task in tasks]
    assert names.index(ASSERT_TASK) > names.index(ROOT_READ_TASK)

    root_read = tasks[names.index(ROOT_READ_TASK)]
    argv = root_read["ansible.builtin.command"]["argv"]
    assert argv[:3] == ["docker", "info", "--format"]
    assert ".DockerRootDir" in argv[3]
    assert root_read["changed_when"] is False

    from_mounts = tasks[names.index(MOUNTS_TASK)]
    assert "ansible_mounts" in str(from_mounts["ansible.builtin.set_fact"])
    assert from_mounts["when"] == "ansible_mounts is defined"

    df_read = tasks[names.index(DF_READ_TASK)]
    assert df_read["ansible.builtin.command"]["argv"][1:3] == ["-B1", "--output=avail"]
    assert df_read["when"] == "ansible_mounts is not defined"


def test_default_free_bytes_floor_is_30_gib():
    defaults = load_defaults()
    floor = defaults["dify_docker_min_free_bytes_before_pull"]
    assert isinstance(floor, int)
    assert floor == 32212254720  # 30 GiB
