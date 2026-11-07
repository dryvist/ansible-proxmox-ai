"""Shared fixtures and render assertions for Pepper egress tests."""
import os
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
from threading import Thread
from urllib.parse import urlsplit

import pytest
import yaml
from jinja2.nativetypes import NativeEnvironment

from _role_files import role_defaults

ROOT = Path(__file__).resolve().parents[2]
MISSING_ENDPOINT_ERROR = "Pepper proxy requires both client endpoint URLs from Pepper's identity domain"
INVALID_HOST_ERROR = 'Pepper egress requires a DNS hostname'

@pytest.fixture
def endpoint_store():
    """Exercise the shared AppRole reader against a loopback-only KV v2 fixture."""
    state = dict(data={}, status=200, requests=[], role_logins=[], read_tokens=[])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state['requests'].append(self.path)
            path = urlsplit(self.path).path
            if path == '/v1/sys/health':
                status, data = 200, {'initialized': True, 'sealed': False, 'standby': False}
            elif path == '/v1/auth/token/lookup-self':
                assert self.headers.get('X-Vault-Token') == 'fixture-approle-token'
                status, data = 200, {'data': {'policies': ['fixture-reader']}}
            elif path == '/v1/secret/data/ai/pepper':
                state['read_tokens'].append(self.headers.get('X-Vault-Token'))
                assert self.headers.get('X-Vault-Token') == 'fixture-approle-token'
                status, data = state['status'], {'data': {'data': state['data'], 'metadata': {}}}
            else:
                status, data = 404, {'errors': ['Unexpected fixture path']}
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_POST(self):
            path = urlsplit(self.path).path
            content_length = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(content_length)
            state['requests'].append(self.path)
            if path == '/v1/auth/approle/login':
                state['role_logins'].append(json.loads(body))
                status, data = 200, {'auth': {
                    'client_token': 'fixture-approle-token',
                    'accessor': 'fixture-accessor',
                    'policies': ['fixture-reader'],
                    'token_policies': ['fixture-reader'],
                    'lease_duration': 3600,
                    'renewable': True,
                }}
            else:
                status, data = 404, {'errors': ['Unexpected fixture path']}
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('localhost', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://localhost:{server.server_port}', state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

def _verify_pepper_proxy_policy(tmp_path, endpoint_store, vault, youtrack, valid, tags, limit,
                                prepared=False, rendered=True, expected_error=None):
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
    domain_groups = NativeEnvironment().from_string(
        roster['openbao_secrets_domain_groups']
    ).render(ai_agents={'pepper': {'container': 'fixture-pepper'}})
    assert domain_groups['pepper'] == ['llm_router_group', 'hermes_agent_group']
    defaults.update(
        agent_guest_proxy_host='proxy.example.test', agent_guest_proxy_port=3128,
        agent_guest_no_proxy='{{ agent_guest_loopback_no_proxy }}',
        hermes_agent_pepper_openbao_host='secrets.example.test',
        openbao_secrets_kv_mount='secret', openbao_secrets_pepper_path=roster['openbao_secrets_pepper_path'],
        openbao_secrets_domain_groups=domain_groups, openbao_secrets_active_groups=[],
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
                 SECRET_STORE_ADDR=address, SECRET_STORE_PEPPER_ROLE_ID='',
                 SECRET_STORE_PEPPER_SECRET_ID='',
                 OPENBAO_APPROLE_PEPPER_ROLE_ID='fixture-role-id',
                 OPENBAO_APPROLE_PEPPER_SECRET_ID='fixture-secret-id',
                 BAO_TOKEN='fixture-converge-token',
                 ANSIBLE_OPENTELEMETRY_ENABLED='true',
                 ANSIBLE_OPENTELEMETRY_STORE_SPANS_IN_FILE='/dev/null'),
        text=True, capture_output=True, check=False)
    assert result.returncode == (0 if valid else 2), result.stdout + result.stderr
    expected_reads = int(prepared) + int(rendered)
    actual_reads = sum(urlsplit(path).path == '/v1/secret/data/ai/pepper' for path in store['requests'])
    assert actual_reads == expected_reads, result.stdout + result.stderr
    assert len(store['role_logins']) == expected_reads
    assert store['role_logins'] == [
        {'role_id': 'fixture-role-id', 'secret_id': 'fixture-secret-id'}
    ] * expected_reads
    assert store['read_tokens'] == ['fixture-approle-token'] * expected_reads
    if valid:
        assert ('Check the identity boundary and shared environment' in result.stdout) is rendered
        preparation = result.stdout.split('PLAY [Verify Pepper egress]')[0]
        assert ('Validate and project each Pepper hostname' in preparation) is prepared
    assert all(urlsplit(path).path in {
        '/v1/sys/health', '/v1/auth/approle/login', '/v1/secret/data/ai/pepper',
        '/v1/auth/token/lookup-self',
    } for path in store['requests'])
    assert 'fixture-password' not in result.stdout + result.stderr
    assert 'fixture-private-token' not in result.stdout + result.stderr
    assert 'fixture-converge-token' not in result.stdout + result.stderr
    if expected_error:
        assert expected_error in result.stdout
