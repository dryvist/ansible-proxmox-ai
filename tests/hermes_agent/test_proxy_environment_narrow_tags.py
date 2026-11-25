"""The isolated proxy environment holds under narrow tag selections.

The runtime install and units imports read `hermes_agent_proxy_environment`,
which agent-egress.yml sets. Ansible's always-tagged role argument check
inherits the import's keywords under every --tags selection, so the role
defaults it to empty for a narrow run (for example `--tags hermes_crons`)
on an isolated guest. The install itself must still get the real proxy.
"""
import os
from pathlib import Path
import subprocess

import yaml

from _role_files import role_defaults

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / 'roles/hermes_agent/tasks/main.yml'


def _imports():
    tasks = yaml.safe_load(MAIN.read_text())
    return {t['ansible.builtin.import_role']['tasks_from']: t for t in tasks
            if isinstance(t.get('ansible.builtin.import_role'), dict)
            and t['ansible.builtin.import_role'].get('tasks_from') in ('install', 'units')}


def test_install_tag_reaches_the_egress_fact_task(tmp_path):
    prep = yaml.safe_load((ROOT / 'playbooks/agent-egress.yml').read_text())[0]
    install_tags = _imports()['install']['tags']
    install_tags = [install_tags] if isinstance(install_tags, str) else install_tags
    assert set(install_tags) & set(prep['tags']), (install_tags, prep['tags'])
    inventory = tmp_path / 'inventory.yml'
    inventory.write_text(yaml.safe_dump(dict(all=dict(children=dict(
        hermes_agent_group=dict(hosts={'fixture-pepper': dict(ansible_connection='local')}),
        squid_group=dict(hosts={'fixture-proxy': dict(ansible_connection='local')}))))))
    env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / 'local'))
    listed = subprocess.run(
        ['ansible-playbook', str(ROOT / 'playbooks/agent-egress.yml'), '-i', str(inventory),
         '--tags', 'hermes_runtime_install', '--list-tasks'],
        env=env, text=True, capture_output=True, check=False)
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert prep['tasks'][0]['name'] in listed.stdout


def test_proxy_expressions_render_from_the_role_default(tmp_path):
    default = role_defaults(ROOT / 'roles/hermes_agent')['hermes_agent_proxy_environment']
    assert default == {}
    imports = _imports()
    expressions = [imports['install']['environment'],
                   imports['units']['vars']['hermes_runtime_gateway_environment']]
    checks = [dict(name=f'Render expression {i}', **{'ansible.builtin.debug': dict(msg=expr)})
              for i, expr in enumerate(expressions)]
    playbook = tmp_path / 'render.yml'
    playbook.write_text(yaml.safe_dump([dict(
        name='Render the proxy expressions on an isolated guest from the role default',
        hosts='localhost', gather_facts=False,
        vars=dict(hermes_agent_is_isolated=True, hermes_agent_github_app_slug='fixture',
                  hermes_agent_github_trust_boundary_default='fixture',
                  hermes_agent_cron_goal_mode_jobs=[], hermes_agent_kanban_goal_max_turns=1,
                  hermes_agent_proxy_environment=default),
        tasks=checks)]))
    env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / 'local'))
    rendered = subprocess.run(['ansible-playbook', str(playbook), '-i', 'localhost,', '-c', 'local'],
                              env=env, text=True, capture_output=True, check=False)
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
