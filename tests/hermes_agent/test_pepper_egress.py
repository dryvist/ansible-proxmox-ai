"""Render the shipped Pepper endpoint projection and shared proxy policy offline."""
import os
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shlex
import subprocess
from threading import Thread

import pytest
import yaml

from _role_files import render_hermes_runtime_unit, role_defaults

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def endpoint_store():
    """Exercise the native lookup against a loopback-only KV v2 fixture."""
    state = dict(data={}, status=200, requests=[])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state['requests'].append(self.path)
            assert self.headers.get('X-Vault-Token') == 'fixture-token'
            if self.path == '/v1/auth/token/lookup-self':
                status, data = 200, {'data': {'policies': ['fixture-reader']}}
            elif self.path == '/v1/secret/data/ai/pepper':
                status, data = state['status'], {'data': {'data': state['data'], 'metadata': {}}}
            else:
                status, data = 404, {'errors': ['Unexpected fixture path']}
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize('tags,limit,prepared,rendered', [
    ([tag], '', True, True) for tag in yaml.safe_load((ROOT / 'playbooks/agent-egress.yml').read_text())[1]['tags']
] + [([], '', True, True), (['squid'], 'squid_group,localhost', False, True),
     (['hermes_units_gateways'], '', True, False), (['llm_router'], '', False, False)])
def test_egress_tag_paths_resolve_endpoints_before_preparation_and_render(
        tmp_path, endpoint_store, tags, limit, prepared, rendered):
    _verify_pepper_proxy_policy(tmp_path, endpoint_store,
                               'https://evidence.example.test/repository.git',
                               'https://issues.example.test/mcp', True, tags, limit, prepared, rendered)


@pytest.mark.parametrize('vault,youtrack,valid', [
    ('https://evidence.example.test/repository.git', 'https://issues.example.test/mcp', True),
    ('https://fixture-user:fixture-password@evidence.example.test/repository.git',
     'https://issues.example.test/mcp?token=fixture-query', True),
    ('', 'https://issues.example.test/mcp', False),
    ('https://evidence.example.test/repository.git', '', False),
    ('https://evidence.example.test/repository.git', 'https://.example.test/mcp', False),
    ('https://evidence.example.test/repository.git', 'https://192.0.2.1/mcp', False),
])
def test_pepper_proxy_policy_is_exclusive_and_uses_endpoint_hosts(tmp_path, endpoint_store, vault, youtrack, valid):
    _verify_pepper_proxy_policy(tmp_path, endpoint_store, vault, youtrack, valid,
                               ['squid'], 'squid_group,localhost')


def _verify_pepper_proxy_policy(tmp_path, endpoint_store, vault, youtrack, valid, tags, limit,
                                prepared=False, rendered=True):
    address, store = endpoint_store
    store['data'] = dict(PEPPER_VAULT_REPO_URL=vault, YOUTRACK_MCP_URL=youtrack,
                         PEPPER_VAULT_READ_TOKEN='fixture-private-token')
    defaults = role_defaults(ROOT / 'roles/agent_guest')
    defaults.update(yaml.safe_load((ROOT / 'roles/squid/defaults/main.yml').read_text()))
    endpoints = dict(
        # No agent facts, secrets or client hostname inventory are available.
        hermes_agent_bao="{{ undef('The proxy cannot use the agent play secret view') }}",
    )
    roster = yaml.safe_load((ROOT / 'inventory/group_vars/all/openbao_secrets/11-roster-domains.yml').read_text())
    defaults.update(
        agent_guest_proxy_host='proxy.example.test', agent_guest_proxy_port=3128,
        agent_guest_no_proxy='{{ agent_guest_loopback_no_proxy }}',
        hermes_agent_pepper_openbao_host='secrets.example.test',
        openbao_secrets_kv_mount='secret', openbao_secrets_pepper_path=roster['openbao_secrets_pepper_path'],
        ansible_managed='fixture',
        tofu_data={'constants': {'service_ports': {'squid_proxy': 3128}}},
    )
    inventory = tmp_path / 'inventory.yml'
    inventory.write_text(yaml.safe_dump(dict(all=dict(children=dict(
        hermes_agent_group=dict(hosts={
            'fixture-pepper': dict(hermes_agent_identity='pepper',
                                   container_reserved_ip='fixture-pepper-address', ansible_connection='local', **endpoints),
            'fixture-ordinary': dict(hermes_agent_identity='hermes',
                                     container_reserved_ip='fixture-ordinary-address')}),
        ai_agent_pool_group=dict(hosts={'fixture-pepper': {}, 'fixture-ordinary': {}}),
        squid_group=dict(hosts={'proxy-fixture': dict(ansible_connection='local')}),
    )))))
    prep, proxy_play = yaml.safe_load((ROOT / 'playbooks/agent-egress.yml').read_text())
    # Keep both production plays' selection and endpoint reads. Exercise the
    # preparation's domain projection without writing guest files or services.
    prep.pop('become')
    prep['vars'] = defaults
    for task in prep.get('pre_tasks', []):
        task['ansible.builtin.include_tasks'] = str(ROOT / 'playbooks' / task['ansible.builtin.include_tasks'])
    prep['tasks'][0]['ansible.builtin.include_role']['tasks_from'] = 'egress_domains'
    fetch = proxy_play['tasks'][0]['block'][0].copy()
    fetch['ansible.builtin.include_tasks'] = str(ROOT / 'playbooks' / fetch['ansible.builtin.include_tasks'])
    resolver = yaml.safe_load((ROOT / 'roles/squid/tasks/main.yml').read_text())[0]
    tasks = [fetch, dict(name='Resolve the production squid inputs', **{'ansible.builtin.include_role': dict(
        name='squid', tasks_from='pepper_egress')},
        **{k: v for k, v in resolver.items() if k in ('loop', 'loop_control', 'when')})]
    tasks += [
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
            "'fixture-password' not in _squid",
            "'fixture-query' not in _squid",
            "'fixture-private-token' not in _squid",
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
            _expected_domains=['.slack.com', '{{ squid_npm_registry_domain }}', 'llm.pve.example.test',
                               'issues.example.test', 'secrets.example.test', 'evidence.example.test'])),
    ]
    playbook = tmp_path / 'verify.yml'
    playbook.write_text(yaml.safe_dump([
        prep,
        dict(name='Verify Pepper egress', hosts='squid_group', gather_facts=False,
             vars=defaults, tasks=tasks, tags=proxy_play['tags']),
    ]))
    result = subprocess.run(
        ['ansible-playbook', str(playbook), '-i', str(inventory), '-c', 'local']
        + (['--tags', ','.join(tags)] if tags else []) + (['--limit', limit] if limit else []),
        env=dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / 'local'),
                 SECRET_STORE_ADDR=address, BAO_TOKEN='fixture-token',
                 ANSIBLE_OPENTELEMETRY_ENABLED='true',
                 ANSIBLE_OPENTELEMETRY_STORE_SPANS_IN_FILE='/dev/null'),
        text=True, capture_output=True, check=False)
    assert result.returncode == (0 if valid else 2), result.stdout + result.stderr
    assert store['requests'].count('/v1/secret/data/ai/pepper') == 2 * (int(prepared) + int(rendered))
    if valid:
        assert ('Check the identity boundary and shared environment' in result.stdout) is rendered
        preparation = result.stdout.split('PLAY [Verify Pepper egress]')[0]
        assert ('Validate and project each Pepper hostname' in preparation) is prepared
    assert set(store['requests']) <= {'/v1/secret/data/ai/pepper', '/v1/auth/token/lookup-self'}
    assert 'fixture-password' not in result.stdout + result.stderr
    assert 'fixture-private-token' not in result.stdout + result.stderr
    if not valid:
        assert 'Pepper egress requires a DNS hostname' in result.stdout


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


@pytest.mark.parametrize('status', [200, 403, 404])
def test_unavailable_client_endpoint_refuses_the_proxy_render(tmp_path, endpoint_store, status):
    address, store = endpoint_store
    store['status'] = status
    # Even a readable path is insufficient when either endpoint field is absent.
    store['data'] = dict(PEPPER_VAULT_REPO_URL='https://evidence.example.test/repository.git')
    roster = yaml.safe_load((ROOT / 'inventory/group_vars/all/openbao_secrets/11-roster-domains.yml').read_text())
    task_file = ROOT / 'playbooks/tasks/pepper_egress_endpoints.yml'
    read = yaml.safe_load(task_file.read_text())[0]['block'][0]
    assert read['delegate_to'] == 'localhost' and read['no_log'] is True
    assert read['become'] is False and read['vars']['ansible_become'] is False
    playbook = tmp_path / 'verify.yml'
    playbook.write_text(yaml.safe_dump([dict(
        name='Verify unavailable Pepper endpoints', hosts='localhost', gather_facts=False,
        vars=dict(openbao_secrets_kv_mount='secret', openbao_secrets_pepper_path=roster['openbao_secrets_pepper_path']),
        tasks=[dict(name='Resolve production endpoint hosts',
                    **{'ansible.builtin.include_tasks': str(task_file)})])]))
    result = subprocess.run(
        ['ansible-playbook', str(playbook), '-i', 'localhost,', '-c', 'local'],
        env=dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / 'local'),
                 SECRET_STORE_ADDR=address, BAO_TOKEN='fixture-token'),
        text=True, capture_output=True, check=False)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Refusing to render the allowlist' in result.stdout
    assert 'evidence.example.test' not in result.stdout + result.stderr


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
