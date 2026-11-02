"""Pin the Hatchet eval router key projection to the shared OpenBao writer."""

from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment


ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = ROOT / "playbooks/llm-serving.yml"
SITE = ROOT / "playbooks/site.yml"
SOURCE_VALUE = (
    "{{(hostvars['localhost'].openbao_secrets_by_domain|default({},true))"
    ".get('llm-router-agent-keys',{}).get('hatchet_eval_llm_router_key','')}}"
)


def _router_play():
    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    return next(play for play in plays if play.get("name") == "Render the LiteLLM router config (all members in parallel)")


def test_publish_runs_with_the_prefetched_domain_and_converge_writer():
    play = _router_play()
    task = next(task for task in play["tasks"] if task.get("name") == "Publish Hatchet eval router key to apps")
    seed_task = task["block"][0]
    include = seed_task["ansible.builtin.include_role"]
    seed_vars = seed_task["vars"]

    assert any(path.endswith("/openbao_secrets/00-seed-vars.yml") for path in play["vars_files"])
    assert any(path.endswith("/openbao_secrets/11-roster-domains.yml") for path in play["vars_files"])
    assert include["name"] == "dryvist.homelab.openbao_secrets"
    assert include["tasks_from"] == "seed_generated"
    assert task["run_once"] is True
    assert task["delegate_to"] == "localhost"
    assert task["become"] is False
    assert task["no_log"] is True
    assert task["tags"] == ["llm_router"]

    assert seed_vars["openbao_secrets_seed_path"] == "apps/hatchet/eval"
    assert seed_vars["openbao_secrets_seed_fields"][0]["name"] == "hatchet_eval_litellm_api_key"
    assert "".join(seed_vars["openbao_secrets_seed_fields"][0]["value"].split()) == SOURCE_VALUE
    assert seed_vars["openbao_secrets_seed_fields"][0]["generate"] is False
    assert seed_vars["openbao_secrets_seed_rotate_fields"][0] == "hatchet_eval_litellm_api_key"
    assert seed_vars["openbao_secrets_seed_token"] == "{{ openbao_secrets_converge_writer_token }}"
    assert seed_vars["openbao_secrets_seed_expected_writer_policy"] == "ansible-converge"
    assert seed_vars["openbao_secrets_seed_check_capabilities"] == "{{ not ansible_check_mode }}"
    assert seed_vars["openbao_secrets_seed_revoke_token"] is False

    site_plays = yaml.safe_load(SITE.read_text(encoding="utf-8"))
    prefetch = next(play for play in site_plays if play.get("name") == "Pre-fetch resource-domain secrets from OpenBao")
    serving = next(play for play in site_plays if play.get("name") == "Configure LLM serving and routing")
    assert site_plays.index(prefetch) < site_plays.index(serving)


def test_absent_roster_key_is_reported_and_skipped():
    play = _router_play()
    publish = next(task for task in play["tasks"] if task.get("name") == "Publish Hatchet eval router key to apps")
    report = next(task for task in play["tasks"] if task.get("name") == "Report the Hatchet eval router key is absent from the roster domain")

    assert "hatchet_eval_llm_router_key" in publish["when"]
    assert "length > 0" in publish["when"]
    assert "hatchet_eval_llm_router_key" in report["when"]
    assert "length == 0" in report["when"]
    assert "absent from" in report["ansible.builtin.debug"]["msg"]

    environment = NativeEnvironment()
    hostvars = {"localhost": {"openbao_secrets_by_domain": {}}}
    publish_condition = environment.from_string("{{ " + publish["when"] + " }}")
    report_condition = environment.from_string("{{ " + report["when"] + " }}")
    assert publish_condition.render(hostvars=hostvars) is False
    assert report_condition.render(hostvars=hostvars) is True
