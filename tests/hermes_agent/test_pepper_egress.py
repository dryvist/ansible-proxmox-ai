"""Render the shipped Pepper endpoint projection and shared proxy policy offline."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from _role_files import role_defaults

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('vault,valid', [
    ('', True), ('https://evidence.example.test/repository.git', True),
    ('http://evidence.example.test/repository.git', False),
    ('https://fixture-user:fixture-password@evidence.example.test/repository.git', False),
])
def test_pepper_proxy_policy_is_exclusive_and_uses_endpoint_hosts(tmp_path, vault, valid):
    defaults = role_defaults(ROOT / 'roles/agent_guest')
    defaults.update(yaml.safe_load((ROOT / 'roles/squid/defaults/main.yml').read_text()))
    defaults.update(
        hermes_agent_model_base_url='https://router.example.test/v1',
        hermes_agent_youtrack_mcp_url='https://issues.example.test/mcp',
        hermes_agent_github_identity_bao_addr='https://secrets.example.test',
        hermes_agent_pepper_vault_repo_url=vault,
        agent_guest_proxy_host='proxy.example.test', agent_guest_proxy_port=3128,
        agent_guest_no_proxy='{{ agent_guest_loopback_no_proxy }}',
        ansible_managed='fixture', inventory_hostname='proxy-fixture',
        tofu_data={'constants': {'service_ports': {'squid_proxy': 3128}}},
    )
    tasks = yaml.safe_load((ROOT / 'roles/hermes_agent/tasks/egress.yml').read_text())[:2]
    tasks += [
        dict(name='Add the Pepper fixture client', **{'ansible.builtin.add_host': dict(
            name='fixture-pepper', groups=['hermes_agent_group', 'ai_agent_pool_group'],
            hermes_agent_identity='pepper', container_reserved_ip='fixture-pepper-address',
            hermes_agent_pepper_egress_domains='{{ hermes_agent_pepper_egress_domains }}')}),
        dict(name='Add the ordinary fixture client', **{'ansible.builtin.add_host': dict(
            name='fixture-ordinary', groups=['hermes_agent_group', 'ai_agent_pool_group'],
            hermes_agent_identity='hermes', container_reserved_ip='fixture-ordinary-address')}),
        dict(name='Render the production proxy templates', **{'ansible.builtin.set_fact': dict(
            _squid="{{ lookup('template', '" + str(ROOT / 'roles/squid/templates/squid.conf.j2') + "') }}",
            _manager="{{ lookup('template', '" + str(ROOT / 'roles/agent_guest/templates/proxy/systemd-proxy.conf.j2')
                     + "') }}",
            _npm="{{ lookup('template', '" + str(ROOT / 'roles/agent_guest/templates/proxy/npmrc.j2') + "') }}")}),
        dict(name='Check the identity boundary and shared environment', **{'ansible.builtin.assert': {'that': [
            "'acl pepper_clients src fixture-pepper-address' in _squid",
            "'acl pepper_clients src fixture-ordinary-address' not in _squid",
            "_squid.index('http_access deny pepper_clients') < _squid.index('http_access allow ai_proxied_clients')",
            "'http_access deny all' in _squid",
            "_pepper_domains | sort == _expected_domains | sort",
            "'api.openai.com' not in _pepper_domains",
            "'api.anthropic.com' not in _pepper_domains",
            "agent_guest_proxy_env.no_proxy == agent_guest_loopback_no_proxy",
            "agent_guest_proxy_env.NO_PROXY == agent_guest_loopback_no_proxy",
            "agent_guest_proxy_env.https_proxy == agent_guest_proxy_url",
            "agent_guest_proxy_url in _manager",
            "agent_guest_loopback_no_proxy in _manager",
            "('noproxy=' ~ agent_guest_loopback_no_proxy) in _npm",
        ]}}, vars=dict(
            _pepper_domains="{{ (_squid.split('acl pepper_domains dstdomain ')[1].split('\n')[0]).split() }}",
            _expected_domains=['.slack.com', '{{ squid_npm_registry_domain }}', 'router.example.test',
                               'issues.example.test', 'secrets.example.test']
                              + (['evidence.example.test'] if vault else []))),
    ]
    playbook = tmp_path / 'verify.yml'
    playbook.write_text(yaml.safe_dump([dict(name='Verify Pepper egress', hosts='localhost',
                                           gather_facts=False, vars=defaults, tasks=tasks)]))
    result = subprocess.run(
        ['ansible-playbook', str(playbook), '-i', 'localhost,', '-c', 'local'],
        env=dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / 'local'),
                 ANSIBLE_OPENTELEMETRY_ENABLED='true',
                 ANSIBLE_OPENTELEMETRY_STORE_SPANS_IN_FILE='/dev/null'),
        text=True, capture_output=True, check=False)
    assert result.returncode == (0 if valid else 2), result.stdout + result.stderr
    if not valid:
        assert vault not in result.stdout + result.stderr


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
    assert wiring['block'][0]['ansible.builtin.include_role'] == dict(
        name='agent_guest', tasks_from='proxy', public=True)
    runtime = yaml.safe_load((ROOT / 'playbooks/agents.yml').read_text())[0]['tasks'][0]['block']
    call = next(t for t in runtime if t['name'] == 'Include hermes_agent role')
    assert call['environment'] == '{{ hermes_agent_proxy_environment | default({}) }}'
    proxy_tasks = yaml.safe_load((ROOT / 'roles/agent_guest/tasks/proxy.yml').read_text())
    assert any(t.get('ansible.builtin.file', {}).get('path') == '{{ agent_guest_npm_prefix }}/etc'
               for t in proxy_tasks)
