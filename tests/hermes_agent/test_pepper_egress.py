"""Render the isolated guest's runtime proxy configuration offline."""
import shlex

import yaml

from _isolated_egress_shared import ROOT
from _role_files import render_hermes_runtime_unit, role_defaults


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
        hermes_agent_pepper_vault_env_path='/fixture/vault.env',
        hermes_agent_pepper_vault_refresh_script_path='/fixture/refresh')
    assert f'EnvironmentFile={proxy_file}' in evidence
    assert 'EnvironmentFile=/fixture/vault.env' in evidence


def test_runtime_install_fetches_through_the_isolated_guest_proxy():
    tasks = yaml.safe_load((ROOT / 'roles/hermes_agent/tasks/main.yml').read_text())
    install = next(t for t in tasks if t.get('name') == 'Install the pinned Hermes runtime')
    assert install['environment'] == (
        '{{ hermes_agent_proxy_environment if hermes_agent_is_isolated else {} }}')
    assert install['vars']['hermes_runtime_installer_skip_browser'] == '{{ hermes_agent_is_isolated }}'
