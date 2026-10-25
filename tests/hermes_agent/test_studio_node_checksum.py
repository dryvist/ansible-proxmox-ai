"""The hermes-studio Node tarball is verified against the upstream SHASUMS list."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/hermes_agent"
TASKS = ROLE / "tasks/companion_uis.yml"
FETCH = "Fetch the pinned Node runtime for hermes-studio (checksum-verified)"


def _fetch_task() -> dict:
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    block = next(
        task["block"] for task in tasks if task.get("name") == "Install and run hermes-studio"
    )
    return next(task for task in block if task.get("name") == FETCH)


def test_node_checksum_is_read_from_the_upstream_shasums_list():
    get_url = _fetch_task()["ansible.builtin.get_url"]
    assert get_url["checksum"] == (
        "sha256:https://nodejs.org/dist/{{ hermes_agent_studio_node_version }}/SHASUMS256.txt"
    )


def test_shasums_entry_name_matches_the_tarball_url():
    get_url = _fetch_task()["ansible.builtin.get_url"]
    url = " ".join(get_url["url"].split())
    assert url.endswith("/node-{{ hermes_agent_studio_node_version }}-linux-x64.tar.xz")


def test_no_pinned_node_sha256_remains_in_the_role():
    offenders = [
        path.relative_to(ROOT)
        for path in (ROOT / "roles").rglob("*")
        if path.is_file()
        and "hermes_agent_studio_node_sha256" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
