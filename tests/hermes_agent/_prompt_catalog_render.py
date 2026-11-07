"""Run the role's prompt tasks on localhost, returning hashes only."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import yaml

from _role_files import role_defaults

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/hermes_agent"


def raw_defaults(role=ROLE):
    merged = {}
    for file in sorted((role / "defaults/main").glob("*.yml")):
        merged.update(yaml.safe_load(file.read_text()) or {})
    return merged


def render_hashes(tmp_path, catalog_path, *, role=ROLE, identity="fixture", overrides=None,
                  include_parts=False, tags=None):
    defaults = raw_defaults(role)
    literal = role_defaults(role)
    keys = json.loads((ROOT / "tests/hermes_agent/fixtures/catalog-prompt-sha256.json").read_text())
    tasks = yaml.safe_load((role / "tasks/prompt_catalog.yml").read_text())
    loaders = [task for task in tasks if "ansible.builtin.set_fact" in task
               and "hermes_agent_prompts_path" not in task["ansible.builtin.set_fact"]]
    direct = next(task for task in yaml.safe_load(
        (role / "tasks/reconcile_direct_cron.yml").read_text())
        if "hermes_agent_direct_prompt" in task.get("ansible.builtin.set_fact", {}))
    direct = deepcopy(direct)
    direct.pop("when", None)
    direct["no_log"] = True
    (tmp_path / "direct.yml").write_text(yaml.safe_dump([
        direct,
        {"name": "Hash rendered cron body", "ansible.builtin.set_fact": {
            "cron_hashes": "{{ cron_hashes + [hermes_agent_direct_prompt | hash('sha256')] }}"},
         "no_log": True},
    ]))
    jobs = []
    for job in literal["hermes_agent_direct_cron_jobs"]:
        if not any(key in job for key in ("prompt_var", "prompt_file", "prompt_files")):
            assert job["enabled"] is False  # retired placeholders have no body
            continue
        jobs.append({"name": str(len(jobs)), **{
            key: value for key, value in job.items()
            if key in ("prompt_var", "prompt_file", "prompt_files", "channel_when_healthy")}})
    if include_parts:
        jobs.append({"name": "parts", **defaults["hermes_agent_catalog_prompt_sources"][
            "hermes_agent_llm_stats_hourly_cron_prompt"]})
    context = {
        **defaults, "hermes_agent_prompts_path": str(catalog_path),
        "hermes_agent_id": identity, "hermes_agent_display_name": identity,
        "hermes_agent_wiki_path": "/fixture/wiki", "hermes_agent_hermes_home": "/fixture/home",
        "hermes_agent_dashboard_port": 8123, "hermes_agent_model_base_url": "https://model.example.test",
        "hermes_agent_app_seeding_endpoints": "fixture app endpoints",
        "hermes_agent_daily_status_endpoints": "fixture status endpoints",
        "hermes_agent_service_pulse_endpoints": "fixture pulse endpoints",
        "hermes_agent_service_pulse_hindsight_endpoint": "fixture memory endpoint",
        "hermes_agent_memory_provider": "hindsight",
        "hermes_agent_direct_deliver": "slack:fixture", "cron_hashes": [], "prompt_hashes": {},
        **(overrides or {}),
    }
    for task in loaders:
        task["no_log"] = True
    (tmp_path / "loaders.yml").write_text(yaml.safe_dump(loaders, sort_keys=False))
    entry = next(task for task in yaml.safe_load((role / "tasks/main.yml").read_text())
                 if task.get("ansible.builtin.import_tasks") == "prompt_catalog.yml")
    entry["ansible.builtin.import_tasks"] = str(tmp_path / "loaders.yml")
    tasks = [
        {"name": "Hash each resolved prompt", "ansible.builtin.set_fact": {
            "prompt_hashes": "{{ prompt_hashes | combine({item: lookup('vars', item) | hash('sha256')}) }}"},
         "loop": list(keys), "no_log": True},
        {"name": "Render every cron body", "ansible.builtin.include_tasks": {
            "file": str(tmp_path / "direct.yml"), "apply": {"tags": [tags or "all"]}},
         "loop": jobs, "loop_control": {"label": "{{ item.name }}"}},
    ]
    if tags:
        tasks.append({"name": "Render the enabled GitHub webhook configuration",
                      "ansible.builtin.set_fact": {
                          "webhook_config": "{{ lookup('ansible.builtin.template', '"
                          + str(role / "templates/config-webhook.yaml.j2") + "') | from_yaml }}"},
                      "vars": {"_webhook_on": True, "_slack_on": False,
                               "hermes_agent_webhook_port": 8644,
                               "hermes_agent_github_route_private_enabled": True,
                               "hermes_agent_github_route_bot_login": "example-bot[bot]"},
                      "no_log": True})
        tasks.append({"name": "Check the catalog prompt in the rendered route",
                      "ansible.builtin.assert": {"that": [
                          "webhook_config.webhook.extra.routes[hermes_agent_github_route_private_name]"
                          ".prompt | hash('sha256') == prompt_hashes.hermes_agent_github_route_prompt"]},
                      "no_log": True})
    tasks += [
        {"name": "Save hashes", "ansible.builtin.copy": {
            "content": "{{ {'prompts': prompt_hashes, 'crons': cron_hashes} | to_json }}",
            "dest": str(tmp_path / "hashes.json"), "mode": "0600"}},
    ]
    if tags:
        for task in tasks:
            task["tags"] = [tags]
    play = [{"name": "Render prompt catalog", "hosts": "localhost", "gather_facts": False,
             "vars": context, "tasks": [entry, *tasks]}]
    (tmp_path / "play.yml").write_text(yaml.safe_dump(play, sort_keys=False))
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", "-c", "local", str(tmp_path / "play.yml"),
         *(["--tags", tags] if tags else [])],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
        env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-local")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads((tmp_path / "hashes.json").read_text())
