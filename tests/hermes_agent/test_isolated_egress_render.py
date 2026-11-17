"""Endpoint-resolution tests for isolated proxied egress."""
import os
import subprocess

import pytest
import yaml
from jinja2.nativetypes import NativeEnvironment

from _isolated_egress_shared import (
    ROOT,
    _verify_isolated_proxy_policy,
    endpoint_store,
)

@pytest.mark.parametrize('tags,limit,prepared,rendered', [
    ([tag], '', True, True) for tag in yaml.safe_load((ROOT / 'playbooks/agent-egress.yml').read_text())[1]['tags']
] + [([], '', True, True), (['hermes_units_gateways'], '', True, False),
     (['llm_router'], '', False, False)])
def test_egress_tag_paths_resolve_endpoints_before_preparation_and_render(
        tmp_path, endpoint_store, tags, limit, prepared, rendered):
    _verify_isolated_proxy_policy(tmp_path, endpoint_store, 'https://issues.example.test/mcp', True,
                                  tags, limit, prepared, rendered)


def test_isolated_endpoint_fetch_survives_production_roster_domain_filter(tmp_path, endpoint_store):
    _verify_isolated_proxy_policy(tmp_path, endpoint_store, 'https://issues.example.test/mcp', True,
                                  ['squid'], 'squid_group,localhost', prepared=False, rendered=True)

@pytest.mark.parametrize('status', [200, 403, 404])
def test_unavailable_client_endpoint_refuses_the_proxy_render(tmp_path, endpoint_store, status):
    address, store = endpoint_store
    store['status'] = status
    # Even a readable path is insufficient when the endpoint URL is absent.
    store['data'] = dict(YOUTRACK_MCP_TOKEN='fixture-private-token')
    task_file = ROOT / 'playbooks/tasks/isolated_egress_endpoints.yml'
    read = yaml.safe_load(task_file.read_text())[0]['block'][0]
    assert read['ansible.builtin.include_role']['name'] == 'dryvist.homelab.openbao_secrets'
    assert read['ansible.builtin.include_role']['apply']['delegate_to'] == 'localhost'
    assert read['ansible.builtin.include_role']['apply']['become'] is False
    assert 'delegate_to' not in read and 'become' not in read
    assert read['vars']['ansible_become'] is False
    # Each isolated identity logs in with its own AppRole and reads only its roster leaf.
    domains = NativeEnvironment().from_string(read['vars']['openbao_secrets_domains']).render(
        _isolated_egress_clients=['example-tracker', 'example-isolated'])
    assert domains == [
        {'name': name, 'role_id_env': f'OPENBAO_APPROLE_{env}_ROLE_ID',
         'secret_id_env': f'OPENBAO_APPROLE_{env}_SECRET_ID', 'paths': [f'ai/{name}'], 'required': True}
        for name, env in [('example-tracker', 'EXAMPLE_TRACKER'), ('example-isolated', 'EXAMPLE_ISOLATED')]
    ]
    assert 'openbao_secrets_domain_groups' in read['vars']['openbao_secrets_active_groups']
    assert read['vars']['openbao_secrets_operator_domains'] == []
    assert 'BAO_TOKEN' not in task_file.read_text()
    inventory = tmp_path / 'inventory.yml'
    inventory.write_text(yaml.safe_dump(dict(all=dict(children=dict(hermes_agent_group=dict(hosts={
        'localhost': dict(hermes_agent_identity='example-tracker', ansible_connection='local')}))))))
    playbook = tmp_path / 'verify.yml'
    playbook.write_text(yaml.safe_dump([dict(
        name='Verify unavailable isolated endpoints', hosts='localhost', gather_facts=False,
        vars=dict(hermes_agent_isolated_hosts=['localhost'],
                  openbao_secrets_domain_groups={'example-tracker': ['llm_router_group', 'hermes_agent_group']}),
        tasks=[dict(name='Resolve production endpoint hosts',
                    **{'ansible.builtin.include_tasks': str(task_file)})])]))
    result = subprocess.run(
        ['ansible-playbook', str(playbook), '-i', str(inventory), '-c', 'local'],
        env=dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / 'local'),
                 SECRET_STORE_ADDR=address,
                 OPENBAO_APPROLE_EXAMPLE_TRACKER_ROLE_ID='fixture-role-id',
                 OPENBAO_APPROLE_EXAMPLE_TRACKER_SECRET_ID='fixture-secret-id',
                 BAO_TOKEN='fixture-converge-token',
                 ANSIBLE_OPENTELEMETRY_ENABLED='true',
                 ANSIBLE_OPENTELEMETRY_STORE_SPANS_IN_FILE='/dev/null'),
        text=True, capture_output=True, check=False)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Refusing to render the allowlist' in result.stdout
    assert 'fixture-private-token' not in result.stdout + result.stderr
