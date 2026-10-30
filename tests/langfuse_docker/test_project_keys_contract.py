"""Keep Langfuse project-key provisioning opt-in and tied to Hermes KV records."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = ROOT / "playbooks/ai-orchestration.yml"


def test_project_key_provisioning_contract():
    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    play = next(play for play in plays if play.get("hosts") == "langfuse_group")
    key_task = next(
        task
        for task in play["tasks"]
        if task.get("name") == "Provision Langfuse project API keys for Hermes agents"
    )
    include = key_task["ansible.builtin.include_role"]
    role_vars = key_task["vars"]

    assert play["vars"]["langfuse_project_keys_agents"] == ["hermes", "donna"]
    assert key_task["loop"] == "{{ langfuse_project_keys_agents }}"
    assert key_task["loop_control"]["loop_var"] == "langfuse_project_keys_agent"
    assert include["name"] == "dryvist.homelab.langfuse_project_keys"
    assert key_task["tags"] == ["never", "langfuse_project_keys"]
    assert include["apply"]["tags"] == ["never", "langfuse_project_keys"]
    assert include["apply"]["delegate_to"] == "localhost"
    assert include["apply"]["become"] is False
    assert key_task["run_once"] is True
    assert key_task["no_log"] is True

    assert role_vars["langfuse_project_keys_base_url"] == "{{ langfuse_docker_nextauth_url }}"
    assert role_vars["langfuse_project_keys_admin_email"] == "{{ langfuse_docker_init_user_email }}"
    assert role_vars["langfuse_project_keys_admin_password"] == "{{ langfuse_docker_init_user_password }}"
    assert role_vars["langfuse_project_keys_org_name"] == "{{ langfuse_docker_init_org }}"
    assert role_vars["langfuse_project_keys_project_name"] == (
        "hermes-{{ langfuse_project_keys_agent }}"
    )
    assert role_vars["langfuse_project_keys_openbao_addr"] == (
        "{{ hostvars['localhost'].openbao_secrets_active_addr }}"
    )
    assert role_vars["langfuse_project_keys_openbao_token"] == "{{ lookup('env', 'BAO_TOKEN') }}"
    assert role_vars["langfuse_project_keys_openbao_mount"] == "{{ openbao_secrets_kv_mount }}"
    assert role_vars["langfuse_project_keys_openbao_path"] == (
        "ai/{{ langfuse_project_keys_agent }}"
    )
    assert role_vars["langfuse_project_keys_public_key_field"] == "LANGFUSE_PUBLIC_KEY"
    assert role_vars["langfuse_project_keys_secret_key_field"] == "LANGFUSE_SECRET_KEY"

    converge_block = play["tasks"][0]
    assert converge_block["tags"] == ["langfuse_docker", "observability", "langfuse_project_keys"]
