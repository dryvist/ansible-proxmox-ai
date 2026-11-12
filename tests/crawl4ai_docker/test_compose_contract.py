from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "crawl4ai_docker"


def test_compose_renders_upstream_single_container_contract() -> None:
    subprocess.run(
        [
            "ansible-playbook",
            "tests/crawl4ai_docker/test_compose_contract.yml",
            "-i",
            "localhost,",
            "-c",
            "local",
        ],
        cwd=REPO_ROOT,
        check=True,
    )


def test_api_token_uses_the_environment_secret_pattern_without_rendering_a_value() -> None:
    defaults = (ROLE_ROOT / "defaults" / "main.yml").read_text()
    tasks = (ROLE_ROOT / "tasks" / "main.yml").read_text()
    handlers = (ROLE_ROOT / "handlers" / "main.yml").read_text()

    assert 'crawl4ai_docker_image: "unclecode/crawl4ai:0.9.5"' in defaults
    assert "crawl4ai_docker_port: 11235" in defaults
    assert "lookup('env', 'CRAWL4AI_API_TOKEN')" in defaults
    assert 'CRAWL4AI_API_TOKEN: "{{ crawl4ai_docker_api_token }}"' in tasks
    assert 'CRAWL4AI_API_TOKEN: "{{ crawl4ai_docker_api_token }}"' in handlers


def test_inventory_tag_targets_the_privileged_role_play() -> None:
    inventory_source = (REPO_ROOT / "inventory/load_tofu/add_lxc_host_groups.yml.j2").read_text()
    orchestration = (REPO_ROOT / "playbooks/ai-orchestration.yml").read_text()

    assert "['crawl4ai_group']" in inventory_source
    assert "if 'crawl4ai' in (item.value.tags | default([]))" in inventory_source
    assert "hosts: crawl4ai_group" in orchestration
    assert "name: crawl4ai_docker" in orchestration
