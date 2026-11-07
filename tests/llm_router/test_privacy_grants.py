"""Exercise the role's managed-grant gate and report-only live-key audit."""

import os
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/llm_router"
MARKER = "sk-fixture-privacy-output-marker"


def run_tasks(tmp_path, records, tasks, **variables):
    playbook = tmp_path / "privacy.yml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Verify privacy grants",
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {
            "_llm_router_virtual_keys_without_values": records,
            "llm_router_model_group_aliases": {"fixture-alias": "fixture-retaining"},
            "llm_router_zdr_unasserted_terminal_rungs": ["fixture-retaining", "fixture-free"],
            "llm_router_zdr_asserted_group_names": ["fixture-local"],
            "llm_router_default_key_generate_params": {"models": ["fixture-local"]},
            "llm_router_default_team_settings": [{
                "team_id": "default-settings", "models": ["fixture-local"],
            }],
            **variables,
        },
        "tasks": [{"ansible.builtin.include_role": {
            "name": str(ROLE), "tasks_from": task,
        }} for task in ([tasks] if isinstance(tasks, str) else tasks)],
    }], sort_keys=False), encoding="utf-8")
    return subprocess.run(
        ["ansible-playbook", str(playbook), "-i", "localhost,", "-c", "local"],
        capture_output=True, text=True, check=False, timeout=60,
        env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-tmp")},
    )


@pytest.mark.parametrize("models", [
    ["fixture-retaining"], ["fixture-free"], ["fixture-alias"], ["*"], ["fixture-*"], ["all-proxy-models"], [],
])
@pytest.mark.parametrize("public", [False, True])
def test_managed_explicit_grants_require_public(tmp_path, models, public):
    key = {"alias": "fixture-existing", "models": models, "metadata": {"app": "fixture-app"}}
    if public:
        key["privacy_class"] = "public"
    result = run_tasks(tmp_path, [key], "assert-privacy-grants.yml")
    assert (result.returncode == 0) == public, result.stdout + result.stderr
    if not public:
        assert "grants require privacy class public" in result.stdout


def test_team_grants_and_default_team(tmp_path):
    for public, default, allowed in [(False, False, False), (True, False, True), (True, True, False)]:
        teams = [{"team_id": "default-settings", "models": ["fixture-local"]}]
        team = {"team_id": "default-settings" if default else "fixture-team",
                "models": ["fixture-retaining"],
                "metadata": {"privacy_class": "public" if public else "sensitive"}}
        teams = [team] if default else teams + [team]
        result = run_tasks(tmp_path, [], "assert-privacy-grants.yml", llm_router_default_team_settings=teams)
        assert (result.returncode == 0) == allowed, result.stdout + result.stderr


def test_generated_key_defaults_remain_zdr_even_with_public_metadata(tmp_path):
    result = run_tasks(tmp_path, [], "assert-privacy-grants.yml", llm_router_default_key_generate_params={
        "models": ["fixture-retaining"], "metadata": {"privacy_class": "public"},
    })
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Default keys and the default team must grant only ZDR" in result.stdout


def test_all_live_keys_audited_without_edits_or_credential_output(tmp_path):
    records = [
        {"key_alias": "fixture-existing", "models": ["fixture-retaining"], "token": MARKER,
         "metadata": {"app": "fixture-app"}},
        {"key_alias": None, "team_id": None, "models": ["fixture-free"], "token": MARKER},
        {"key_alias": "fixture-public", "models": ["fixture-retaining"],
         "metadata": {"privacy_class": "public"}},
        {"key_alias": "fixture-public-tag", "models": ["fixture-free"],
         "metadata": {"tags": ["&sensitivity:public"]}},
        {"key_alias": "fixture-local", "models": ["fixture-local"]},
    ]
    result = run_tasks(tmp_path, [], "report-key-privacy.yml", llm_router_existing_keys={"json": {"keys": records}})
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "WARNING: key fixture-existing grants fixture-retaining" in output
    assert "WARNING: key unnamed-key-2 grants fixture-free" in output
    assert "WARNING: key fixture-public" not in output
    assert "WARNING: key fixture-local" not in output
    assert MARKER not in output
    assert "changed=0" in output
    tasks = yaml.safe_load((ROLE / "tasks/report-key-privacy.yml").read_text())
    assert all("ansible.builtin.uri" not in task for task in tasks)
    seed = (ROLE / "tasks/seed-keys.yml").read_text()
    assert seed.index("seed-key-pages.yml") < seed.index("report-key-privacy.yml")


def test_default_team_seed_uses_native_api_before_key_creation():
    tasks = yaml.safe_load((ROLE / "tasks/seed-teams.yml").read_text())
    read, write = tasks
    assert "/team/info?team_id=" in read["ansible.builtin.uri"]["url"]
    assert read["ansible.builtin.uri"]["status_code"] == [200, 404]
    assert read["check_mode"] is False
    assert write["ansible.builtin.uri"]["body"] == "{{ _target }}"
    assert "'new' if item.status == 404 else 'update'" in write["ansible.builtin.uri"]["url"]
    assert "_live.models" in write["when"]
    assert "_target.metadata" in write["when"]
    assert "_target.router_settings" in write["when"]
    assert read["no_log"] and write["no_log"]
    seed = yaml.safe_load((ROLE / "tasks/seed-keys.yml").read_text())
    team_index = next(i for i, task in enumerate(seed)
                      if task.get("ansible.builtin.import_tasks") == "seed-teams.yml")
    key_index = next(i for i, task in enumerate(seed)
                     if "/key/generate" in task.get("ansible.builtin.uri", {}).get("url", ""))
    gate_index = next(i for i, task in enumerate(seed)
                     if task.get("ansible.builtin.import_tasks") == "assert-privacy-grants.yml")
    assert gate_index < team_index
    assert team_index < key_index
