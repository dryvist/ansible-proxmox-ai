"""Endpoint-resolution tests for Pepper's proxied egress."""
import os
import subprocess

import pytest
import yaml
from jinja2.nativetypes import NativeEnvironment

from _pepper_egress_shared import (
    ROOT,
    _verify_pepper_proxy_policy,
    endpoint_store,
    pepper_roster,
    pepper_secret_paths,
)

@pytest.mark.parametrize('tags,limit,prepared,rendered', [
    ([tag], '', True, True) for tag in yaml.safe_load((ROOT / 'playbooks/agent-egress.yml').read_text())[1]['tags']
] + [([], '', True, True), (['hermes_units_gateways'], '', True, False),
     (['llm_router'], '', False, False)])
def test_egress_tag_paths_resolve_endpoints_before_preparation_and_render(
        tmp_path, endpoint_store, tags, limit, prepared, rendered):
    _verify_pepper_proxy_policy(tmp_path, endpoint_store,
                               'https://evidence.example.test/repository.git',
                               'https://issues.example.test/mcp', True, tags, limit, prepared, rendered)


def test_pepper_endpoint_fetch_survives_production_roster_domain_filter(tmp_path, endpoint_store):
    _verify_pepper_proxy_policy(tmp_path, endpoint_store,
                               'https://evidence.example.test/repository.git',
                               'https://issues.example.test/mcp', True,
                               ['squid'], 'squid_group,localhost', prepared=False, rendered=True)

@pytest.mark.parametrize('status', [200, 403, 404])
def test_unavailable_client_endpoint_refuses_the_proxy_render(tmp_path, endpoint_store, status):
    address, store = endpoint_store
    store['status'] = status
    # Even a readable path is insufficient when either endpoint field is absent.
    store['data'] = dict(PEPPER_VAULT_REPO_URL='https://evidence.example.test/repository.git')
    roster = pepper_roster()
    pepper_paths = pepper_secret_paths(roster)
    task_file = ROOT / 'playbooks/tasks/pepper_egress_endpoints.yml'
    read = yaml.safe_load(task_file.read_text())[0]['block'][0]
    assert read['ansible.builtin.include_role']['name'] == 'dryvist.homelab.openbao_secrets'
    assert read['ansible.builtin.include_role']['apply']['delegate_to'] == 'localhost'
    assert read['ansible.builtin.include_role']['apply']['become'] is False
    assert 'delegate_to' not in read and 'become' not in read
    assert read['vars']['ansible_become'] is False
    domains = read['vars']['openbao_secrets_domains']
    assert domains == [{
        'name': 'pepper',
        'role_id_env': 'OPENBAO_APPROLE_PEPPER_ROLE_ID',
        'secret_id_env': 'OPENBAO_APPROLE_PEPPER_SECRET_ID',
        'paths': '{{ openbao_secrets_pepper_paths }}',
        'required': True,
    }]
    assert NativeEnvironment().from_string(domains[0]['paths']).render(
        openbao_secrets_pepper_paths=pepper_paths) == pepper_paths
    assert read['vars']['openbao_secrets_active_groups'] == (
        "{{ openbao_secrets_domain_groups.get('pepper', []) }}")
    assert read['vars']['openbao_secrets_operator_domains'] == []
    assert 'BAO_TOKEN' not in task_file.read_text()
    playbook = tmp_path / 'verify.yml'
    playbook.write_text(yaml.safe_dump([dict(
        name='Verify unavailable Pepper endpoints', hosts='localhost', gather_facts=False,
        vars=dict(openbao_secrets_pepper_paths=pepper_paths),
        tasks=[dict(name='Resolve production endpoint hosts',
                    **{'ansible.builtin.include_tasks': str(task_file)})])]))
    result = subprocess.run(
        ['ansible-playbook', str(playbook), '-i', 'localhost,', '-c', 'local'],
        env=dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / 'local'),
                 SECRET_STORE_ADDR=address, SECRET_STORE_PEPPER_ROLE_ID='',
                 SECRET_STORE_PEPPER_SECRET_ID='',
                 OPENBAO_APPROLE_PEPPER_ROLE_ID='fixture-role-id',
                 OPENBAO_APPROLE_PEPPER_SECRET_ID='fixture-secret-id',
                 BAO_TOKEN='fixture-converge-token',
                 ANSIBLE_OPENTELEMETRY_ENABLED='true',
                 ANSIBLE_OPENTELEMETRY_STORE_SPANS_IN_FILE='/dev/null'),
        text=True, capture_output=True, check=False)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Refusing to render the allowlist' in result.stdout
    assert 'evidence.example.test' not in result.stdout + result.stderr
