"""Render the shipped gateway proxy projection under its isolated unit tag."""
import os
from pathlib import Path
import subprocess

import yaml

from _role_files import hermes_runtime_role_root, role_defaults

ROOT = Path(__file__).resolve().parents[2]


def test_gateway_tag_prepares_and_renders_the_pepper_proxy_environment(tmp_path):
    prep = yaml.safe_load((ROOT / 'playbooks/agent-egress.yml').read_text())[0]
    wiring = yaml.safe_load((ROOT / 'roles/hermes_agent/tasks/egress.yml').read_text())[-1]
    runtime = next(t for t in yaml.safe_load((ROOT / 'roles/hermes_agent/tasks/main.yml').read_text())
                   if t.get('ansible.builtin.import_role', {}).get('tasks_from') == 'units')
    defaults = role_defaults(ROOT / 'roles/agent_guest')
    defaults.update(role_defaults(ROOT / 'roles/hermes_agent'))
    defaults.update(
        hermes_agent_identity='pepper', hermes_agent_id='pepper', ansible_managed='fixture',
        agent_guest_proxy_host='proxy.example.test', agent_guest_proxy_port=3128,
        hermes_agent_uid=1001, hermes_agent_dashboard_port=8080,
    )
    fixture = tmp_path / 'verify.yml'
    # Exercise the production fact task and include-role vars. Guest file writes
    # and service starts are outside this offline template test.
    fixture.write_text(yaml.safe_dump([
        dict(name=prep['name'], hosts='hermes_agent_group', gather_facts=False,
             tags=prep['tags'], vars=defaults,
             tasks=[dict(wiring['block'][-1], vars=wiring['vars'])]),
        dict(name='Render the production gateway unit', hosts='hermes_agent_group', gather_facts=False,
             tags=runtime['tags'], vars=dict(defaults, **runtime['vars']), tasks=[
                 dict(name='Capture the gateway unit', **{'ansible.builtin.set_fact': dict(
                     _unit="{{ lookup('template', '" + str(hermes_runtime_role_root()
                             / 'templates/hermes-gateway.service.j2') + "') }}")}),
                 dict(name='Verify the gateway uses the restrictive proxy', **{'ansible.builtin.assert': dict(that=[
                     "'Environment=https_proxy=http://proxy.example.test:3128' in _unit",
                     "'Environment=HTTPS_PROXY=http://proxy.example.test:3128' in _unit",
                     "'Environment=no_proxy=localhost,127.0.0.1,::1' in _unit",
                     "'Environment=NO_PROXY=localhost,127.0.0.1,::1' in _unit",
                 ])}),
             ]),
    ]))
    inventory = tmp_path / 'inventory.yml'
    inventory.write_text(yaml.safe_dump(dict(all=dict(children=dict(
        hermes_agent_group=dict(hosts={'fixture-pepper': dict(ansible_connection='local')}),
        squid_group=dict(hosts={'fixture-proxy': dict(ansible_connection='local')}))))))
    env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / 'local'),
               ANSIBLE_OPENTELEMETRY_ENABLED='true', ANSIBLE_OPENTELEMETRY_STORE_SPANS_IN_FILE='/dev/null')
    # Native tag selection on the actual play must reach the role that produces
    # those facts; the render above uses that play's unchanged tag inheritance.
    selected = subprocess.run(
        ['ansible-playbook', str(ROOT / 'playbooks/agent-egress.yml'), '-i', str(inventory),
         '--tags', 'hermes_units_gateways', '--list-tasks'],
        env=env, text=True, capture_output=True, check=False)
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert prep['tasks'][0]['name'] in selected.stdout
    rendered = subprocess.run(
        ['ansible-playbook', str(fixture), '-i', str(inventory), '-c', 'local',
         '--tags', 'hermes_units_gateways'], env=env, text=True, capture_output=True, check=False)
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    assert 'Verify the gateway uses the restrictive proxy' in rendered.stdout
