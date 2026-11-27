"""Render the isolated proxy policy offline."""
import pytest
import yaml

from _isolated_egress_shared import (
    INVALID_HOST_ERROR,
    MISSING_ENDPOINT_ERROR,
    ROOT,
    _verify_isolated_proxy_policy,
    endpoint_store,
)

@pytest.mark.parametrize('youtrack,valid,expected_error', [
    ('https://issues.example.test/mcp', True, None),
    ('https://fixture-user:fixture-password@issues.example.test/mcp?token=fixture-query', True, None),
    ('', False, MISSING_ENDPOINT_ERROR),
    ('https://.example.test/mcp', False, INVALID_HOST_ERROR),
    ('https://192.0.2.1/mcp', False, INVALID_HOST_ERROR),
])
def test_isolated_proxy_policy_is_exclusive_and_uses_endpoint_hosts(
        tmp_path, endpoint_store, youtrack, valid, expected_error):
    _verify_isolated_proxy_policy(tmp_path, endpoint_store, youtrack, valid,
                                  ['squid'], 'squid_group,localhost', expected_error=expected_error)

@pytest.mark.parametrize('tags,limit,prepared', [(['squid'], 'squid_group,localhost', False), ([], '', True)])
def test_each_isolated_identity_gets_its_own_allowlist(tmp_path, endpoint_store, tags, limit, prepared):
    _verify_isolated_proxy_policy(tmp_path, endpoint_store, 'https://issues.example.test/mcp', True, tags, limit,
                                  prepared=prepared, second='https://tracker.example.test/mcp')


def test_proxy_precedes_runtime_and_uses_the_shared_guest_role():
    site = yaml.safe_load((ROOT / 'playbooks/site.yml').read_text())
    imports = [p.get('import_playbook') for p in site]
    assert imports.index('agent-egress.yml') < imports.index('agents.yml') < imports.index('agent-runners.yml')
    plays = yaml.safe_load((ROOT / 'playbooks/agent-egress.yml').read_text())
    assert [p['hosts'] for p in plays] == ['hermes_agent_group', 'squid_group']
    assert plays[0]['tasks'][0]['when'] == (
        'inventory_hostname in hermes_agent_isolated_hosts and not '
        '(hermes_agent_delivery_probe_only | default(false) | bool)'
    )
    egress = yaml.safe_load((ROOT / 'roles/hermes_agent/tasks/egress.yml').read_text())
    wiring = egress[-1]
    assert wiring['vars']['agent_guest_no_proxy'] == '{{ agent_guest_loopback_no_proxy }}'
    assert wiring['vars']['agent_guest_proxy_system_wide'] is False
    assert wiring['block'][0]['ansible.builtin.include_role'] == dict(
        name='agent_guest', tasks_from='proxy', public=True)
    runtime = yaml.safe_load((ROOT / 'playbooks/agents.yml').read_text())[0]['tasks'][0]['block']
    call = next(t for t in runtime if t['name'] == 'Include hermes_agent role')
    assert 'environment' not in call
    proxy_tasks = yaml.safe_load((ROOT / 'roles/agent_guest/tasks/proxy_guest.yml').read_text())
    assert any(t.get('ansible.builtin.file', {}).get('path') == '{{ agent_guest_npm_prefix }}/etc'
               for t in proxy_tasks)
