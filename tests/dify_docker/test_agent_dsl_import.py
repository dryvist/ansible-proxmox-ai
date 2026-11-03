"""Agent DSL import: pinned sources, the seven names, and which console imports them."""

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
PROD_GROUP = ROOT / "inventory/group_vars/dify_group.yml"
EVAL_GROUP = ROOT / "inventory/group_vars/dify_eval_group.yml"
TASKS = ROOT / "roles/dify_docker/tasks"

EXPECTED_NAMES = [
    "ci-fixer",
    "code-simplifier",
    "doc-contradiction-finder",
    "docs-updater",
    "vikunja-clerk",
    "vikunja-triage",
    "zammad-triage",
]


def _defaults():
    return yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))


def test_seven_agent_dsls_pinned_to_commit_with_sha256():
    defaults = _defaults()
    assert re.fullmatch(r"[0-9a-f]{40}", defaults["dify_docker_agent_dsl_commit"])
    dsls = defaults["dify_docker_agent_dsls"]

    assert [entry["name"] for entry in dsls] == EXPECTED_NAMES
    for entry in dsls:
        assert entry["url"] == "{{ dify_docker_agent_dsl_base }}/" + entry["name"] + ".dsl.yml", entry["name"]
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry["name"]


def test_import_is_off_by_default_and_on_only_for_production():
    assert _defaults()["dify_docker_import_agent_dsls"] is False
    assert yaml.safe_load(PROD_GROUP.read_text(encoding="utf-8"))["dify_docker_import_agent_dsls"] is True
    eval_group = yaml.safe_load(EVAL_GROUP.read_text(encoding="utf-8")) or {}
    assert "dify_docker_import_agent_dsls" not in eval_group


def test_import_tasks_are_wired_and_never_overwrite():
    wiring = yaml.safe_load((TASKS / "main.yml").read_text(encoding="utf-8"))
    included = [task.get("ansible.builtin.include_tasks") for task in wiring]
    assert "import_agent_dsls.yml" in included

    per_app = (TASKS / "import_agent_dsl.yml").read_text(encoding="utf-8")
    # Skips an existing name rather than importing over it.
    assert "dify_docker_dsl_name not in" in per_app
    # Imports as YAML content, after get_url verifies the pinned sha256 on the bytes.
    assert "mode: yaml-content" in per_app
    assert 'checksum: "sha256:{{ dify_docker_agent_dsl.sha256 }}"' in per_app
    # Confirms only a pending import, never blindly.
    assert "== 'pending'" in per_app
