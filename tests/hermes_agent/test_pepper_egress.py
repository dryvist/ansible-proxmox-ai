"""Render Pepper's shared proxy policy and runtime configuration offline."""
import shlex

import pytest
import yaml

from _pepper_egress_shared import (
    INVALID_HOST_ERROR,
    MISSING_ENDPOINT_ERROR,
    ROOT,
    _verify_pepper_proxy_policy,
    endpoint_store,
)
from _role_files import render_hermes_runtime_unit, role_defaults

@pytest.mark.parametrize('vault,youtrack,valid,expected_error', [
    ('https://evidence.example.test/repository.git', 'https://issues.example.test/mcp', True, None),
    ('https://fixture-user:fixture-password@evidence.example.test/repository.git',
     'https://issues.example.test/mcp?token=fixture-query', True, None),
    ('', 'https://issues.example.test/mcp', False, MISSING_ENDPOINT_ERROR),
    ('https://evidence.example.test/repository.git', '', False, MISSING_ENDPOINT_ERROR),
    ('https://evidence.example.test/repository.git', 'https://.example.test/mcp', False, INVALID_HOST_ERROR),
    ('https://evidence.example.test/repository.git', 'https://192.0.2.1/mcp', False, INVALID_HOST_ERROR),
])
def test_pepper_proxy_policy_is_exclusive_and_uses_endpoint_hosts(
        tmp_path, endpoint_store, vault, youtrack, valid, expected_error):
    _verify_pepper_proxy_policy(tmp_path, endpoint_store, vault, youtrack, valid,
                               ['squid'], 'squid_group,localhost', expected_error=expected_error)

def test_proxy_precedes_runtime_and_uses_the_shared_guest_role():
    site = yaml.safe_load((ROOT / 'playbooks/site.yml').read_text())
    imports = [p.get('import_playbook') for p in site]
    assert imports.index('agent-egress.yml') < imports.index('agents.yml') < imports.index('agent-runners.yml')
    plays = yaml.safe_load((ROOT / 'playbooks/agent-egress.yml').read_text())
    assert [p['hosts'] for p in plays] == ['hermes_agent_group', 'squid_group']
    assert plays[0]['tasks'][0]['when'] == "hermes_agent_identity | default('') == 'pepper'"
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


def test_install_tasks_and_runtime_units_use_different_proxy_settings():
    """Render the actual shared gateway unit with the consumer's proxy input."""
    proxy = dict(https_proxy='http://proxy.example.test:3128',
                 HTTPS_PROXY='http://proxy.example.test:3128',
                 no_proxy='localhost,127.0.0.1,::1', NO_PROXY='localhost,127.0.0.1,::1')
    role = ROOT / 'roles/hermes_agent'
    for identity in ['pepper', 'donna']:
        unit = render_hermes_runtime_unit(role, 'hermes-gateway.service.j2',
                                          hermes_agent_id=identity, hermes_agent_proxy_environment=proxy)
        for name, value in proxy.items():
            assert (f'Environment={name}={value}' in unit) == (identity == 'pepper')
    proxy_tasks = yaml.safe_load((ROOT / 'roles/agent_guest/tasks/proxy.yml').read_text())
    guest_wide, service_only = proxy_tasks[:2]
    assert guest_wide['when'] == 'agent_guest_proxy_system_wide | bool'
    assert service_only['when'] == 'not (agent_guest_proxy_system_wide | bool)'
    cleanup = service_only['block'][0]
    assert cleanup['ansible.builtin.file']['state'] == 'absent'
    assert '{{ agent_guest_systemd_proxy_conf }}' in cleanup['loop']
    assert '{{ agent_guest_npm_prefix }}/etc/npmrc' in cleanup['loop']
    assert cleanup['notify'] == 'Re-exec systemd for the proxy environment'
    assert proxy_tasks[-1]['ansible.builtin.meta'] == 'flush_handlers'

    from ansible.plugins.filter.core import FilterModule
    from jinja2 import Environment, StrictUndefined
    env = Environment(undefined=StrictUndefined)
    env.filters.update(FilterModule().filters())
    environment = env.from_string((ROOT / 'roles/agent_guest/templates/proxy/runtime.env.j2').read_text()).render(
        ansible_managed='fixture', agent_guest_proxy_env=proxy)
    assert dict(line.split('=', 1) for line in shlex.split(environment, comments=True)) == proxy
    proxy_file = role_defaults(ROOT / 'roles/agent_guest')['agent_guest_proxy_environment_file']
    evidence = env.from_string((role / 'templates/hermes-pepper-vault.service.j2').read_text()).render(
        ansible_managed='fixture', hermes_agent_proxy_environment_file=proxy_file,
        hermes_agent_pepper_vault_path='/fixture/evidence',
        hermes_agent_pepper_vault_repo_url='https://evidence.example.test/repository.git')
    assert f'EnvironmentFile={proxy_file}' in evidence
