from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "crawl4ai_docker"
SECRET_DOMAINS = REPO_ROOT / "inventory/group_vars/all/openbao_secrets/10-domains.yml"


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
        env={**os.environ, "CRAWL4AI_API_TOKEN": "crawl4ai-env-test-token"},
        check=True,
    )


def test_api_token_prefers_bao_and_falls_back_to_the_environment() -> None:
    defaults = yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text(encoding="utf-8"))
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "main.yml").read_text(encoding="utf-8"))
    tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text(encoding="utf-8")
    handlers = (ROLE_ROOT / "handlers" / "main.yml").read_text()

    assert defaults["crawl4ai_docker_image"] == "unclecode/crawl4ai:0.9.5"
    assert defaults["crawl4ai_docker_port"] == 11235
    assert " ".join(defaults["crawl4ai_docker_api_token"].split()) == (
        "{{ (bao_apps_secrets | default({})).crawl4ai_api_token "
        "| default(lookup('env', 'CRAWL4AI_API_TOKEN'), true) }}"
    )
    assert tasks[0]["ansible.builtin.assert"]["fail_msg"] == (
        "crawl4ai_docker_api_token must be supplied by "
        "bao_apps_secrets.crawl4ai_api_token or CRAWL4AI_API_TOKEN."
    )
    assert 'CRAWL4AI_API_TOKEN: "{{ crawl4ai_docker_api_token }}"' in tasks_text
    assert 'CRAWL4AI_API_TOKEN: "{{ crawl4ai_docker_api_token }}"' in handlers


def test_crawl4ai_secret_leaf_is_declared_in_the_apps_domain() -> None:
    domains = yaml.safe_load(SECRET_DOMAINS.read_text(encoding="utf-8"))
    apps = next(
        domain
        for domain in domains["_openbao_secrets_static_domains_fleet"]
        if domain["name"] == "apps"
    )

    assert {"path": "apps/crawl4ai", "optional": True} in apps["paths"]


def test_apps_domain_is_prefetched_for_crawl4ai_before_its_role_runs() -> None:
    site = yaml.safe_load((REPO_ROOT / "playbooks/site.yml").read_text(encoding="utf-8"))
    orchestration = yaml.safe_load((REPO_ROOT / "playbooks/ai-orchestration.yml").read_text(encoding="utf-8"))
    prefetch = next(play for play in site if play.get("name") == "Pre-fetch resource-domain secrets from OpenBao")
    orchestration_import = next(play for play in site if play.get("import_playbook") == "ai-orchestration.yml")
    crawl4ai = next(
        play for play in orchestration if play.get("name") == "Configure Crawl4AI web crawling API"
    )

    assert "crawl4ai_group" in prefetch["hosts"].split(":")
    assert site.index(prefetch) < site.index(orchestration_import)
    assert crawl4ai["hosts"] == "crawl4ai_group"


def test_inventory_tag_targets_the_privileged_role_play() -> None:
    inventory_source = (REPO_ROOT / "inventory/load_tofu/add_lxc_host_groups.yml.j2").read_text()
    orchestration = (REPO_ROOT / "playbooks/ai-orchestration.yml").read_text()

    assert "['crawl4ai_group']" in inventory_source
    assert "if 'crawl4ai' in (item.value.tags | default([]))" in inventory_source
    assert "hosts: crawl4ai_group" in orchestration
    assert "name: crawl4ai_docker" in orchestration
