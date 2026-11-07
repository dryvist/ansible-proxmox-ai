"""Pepper prefetch follows the selected runtime rollout."""
from pathlib import Path
import os
import subprocess

import pytest
import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('identities', [[], ['hermes'], ['hermes-private'], ['donna'],
                                        ['hermes', 'donna'], ['pepper'], ['hermes', 'pepper']])
@pytest.mark.parametrize('run_tags,skip_tags,selected', [
    (['all'], [], True), (['llm_router'], [], False),
    (['hermes_agent'], [], True), (['llm'], [], True),
    (['agent'], [], True), (['ai'], [], True),
    (['hermes_profiles_env'], [], True), (['hermes_patches'], [], True),
    (['hermes_crons'], [], True), (['hermes_runtime_install'], [], True),
    (['hermes_units_gateways'], [], True), (['hermes_content_scripts'], [], True),
    (['hermes_delivery_probe'], [], True), (['tagged'], [], True),
    (['squid'], [], False), (['squid'], ['squid'], False), (['squid'], ['hermes_agent'], False),
    (['untagged'], [], False), (['always'], [], False),
    (['all'], ['hermes_agent'], False), (['hermes_agent'], ['ai'], False),
    (['hermes_profiles_env'], ['hermes_profiles_env'], False),
    (['llm_router', 'hermes_profiles_env'], [], True),
    (['all'], ['all'], False), (['all'], ['tagged'], False),
    (['all'], ['hermes_crons'], True),
    (['all'], ['hermes_runtime_install', 'hermes_content_scripts', 'hermes_profiles_env',
               'hermes_patches', 'hermes_crons', 'hermes_units_gateways', 'hermes_delivery_probe'], False),
])
def test_pepper_secret_domain_is_selected_only_for_its_runtime(identities, run_tags, skip_tags, selected):
    directory = ROOT / 'inventory/group_vars/all/openbao_secrets'
    identity_domains = yaml.safe_load((directory / '10-domains-identities.yml').read_text())
    fleet = yaml.safe_load((directory / '10-domains.yml').read_text())
    roster = yaml.safe_load((directory / '11-roster-domains.yml').read_text())
    static = (fleet['_openbao_secrets_static_domains_fleet']
              + identity_domains['_openbao_secrets_static_domains_identities'])
    # Include an unprovisioned roster entry: only the selected runtime may require its domain.
    context = dict(_openbao_secrets_static_domains=static, ai_agents={'pepper': {'container': 'fixture'}},
                   ai_agent_owner={}, ai_callers={}, openbao_app_agent_tier_reader_domains=[],
                   ansible_play_hosts_all=identities, ansible_run_tags=run_tags, ansible_skip_tags=skip_tags,
                   hostvars={identity: {'hermes_agent_identity': identity} for identity in identities})
    env = NativeEnvironment(undefined=StrictUndefined)
    env.filters.update(intersect=lambda a, b: list(set(a) & set(b)),
                       difference=lambda a, b: list(set(a) - set(b)), bool=bool)
    context['hermes_agent_pepper_play_tags'] = roster['hermes_agent_pepper_play_tags']
    context['hermes_agent_pepper_runtime_selected'] = env.from_string(
        roster['hermes_agent_pepper_runtime_selected']).render(context)
    assert context['hermes_agent_pepper_runtime_selected'] is selected
    domains = env.from_string(roster['openbao_secrets_domains']).render(context)
    pepper = [d for d in domains if d['name'] == 'pepper']
    assert pepper == ([{'name': 'pepper', 'paths': ['ai/pepper'], 'required': True}]
                      if 'pepper' in identities and selected else [])
    assert [d for d in domains if d['name'] in {s['name'] for s in static}] == static
    assert all('ai/pepper' not in d['paths'] for d in domains if d['name'] != 'pepper')
    play = yaml.safe_load((ROOT / 'playbooks/agents.yml').read_text())[0]
    runtime = next(task for task in play['tasks'][0]['block'] if task['name'] == 'Include hermes_agent role')
    for identity in ['pepper', 'hermes', 'hermes-private', 'donna']:
        allowed = env.from_string('{{ ' + runtime['when'] + ' }}').render(
            context, hermes_agent_identity=identity)
        assert allowed is (selected or identity != 'pepper')



def test_rollout_tag_gate_tracks_the_runtime_entry_points():
    roster = (ROOT / 'inventory/group_vars/all/openbao_secrets/11-roster-domains.yml').read_text()
    agents = yaml.safe_load((ROOT / 'playbooks/agents.yml').read_text())
    role_tags = yaml.safe_load((ROOT / 'roles/hermes_agent/defaults/main/196-entry-point-tags.yml').read_text())
    for tag in set(agents[0]['tags']) | set(role_tags['hermes_agent_entry_point_task_tags'].values()):
        assert repr(tag) in roster


@pytest.mark.parametrize('tags,skip,selected', [
    ('llm_router', '', False), ('hermes_profiles_env', '', True), ('squid', '', False),
    ('all', 'hermes_agent', False), ('all', '', True),
])
def test_cli_tag_selection_gates_always_tagged_runtime_prerequisites(tmp_path, tags, skip, selected):
    play = yaml.safe_load((ROOT / 'playbooks/agents.yml').read_text())[0]
    runtime = next(task for task in play['tasks'][0]['block'] if task['name'] == 'Include hermes_agent role')
    # An unavailable runtime prerequisite must fail only when Pepper is selected.
    fixture = tmp_path / 'verify.yml'
    fixture.write_text(yaml.safe_dump([dict(
        name='Verify Pepper runtime tag selection', hosts='localhost', gather_facts=False,
        vars=dict(hermes_agent_identity='pepper'),
        vars_files=[str(ROOT / 'inventory/group_vars/all/openbao_secrets/11-roster-domains.yml')],
        tasks=[dict(name='Require fixture runtime provisioning', tags='always',
                    when=runtime['when'], **{'ansible.builtin.assert': {'that': ['false']}})])]))
    result = subprocess.run(
        ['ansible-playbook', str(fixture), '-i', 'localhost,', '-c', 'local',
         '--tags', tags, '--skip-tags', skip],
        env=dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / 'local'),
                 ANSIBLE_OPENTELEMETRY_ENABLED='true',
                 ANSIBLE_OPENTELEMETRY_STORE_SPANS_IN_FILE='/dev/null'),
        text=True, capture_output=True, check=False)
    assert result.returncode == (2 if selected else 0), result.stdout + result.stderr
    assert '[WARNING]' not in result.stdout + result.stderr
