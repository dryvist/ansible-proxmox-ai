"""Pepper's research posture and its client wiring never coexist."""
import re

import pytest
import yaml
from jinja2.nativetypes import NativeEnvironment

from _pepper_identity_shared import ROOT, ROLE, resolve
from _role_files import role_defaults
from test_context_limits import _render

CLIENT_DENIED = ['terminal', 'code_execution', 'web', 'search', 'browser', 'file',
                 'memory', 'skills', 'delegation', 'project', 'desktop_ui', 'catalog']
RESEARCH_LIFTED = {'web', 'search', 'file', 'memory', 'skills'}


def _config(identity, research):
    defaults = role_defaults(ROLE)
    _, config = _render(
        'config.yaml.j2', {}, hermes_agent_id=identity,
        hermes_agent_pepper_research_enabled=research,
        hermes_agent_memory_provider='' if identity == 'pepper' else 'hindsight',
        hermes_agent_kanban_goal_mode=identity != 'pepper',
        hermes_agent_api_server_key='fixture', hermes_agent_slack_bot_token='fixture',
        hermes_agent_api_max_concurrent_runs=1,
        hermes_agent_api_server_toolsets=defaults['hermes_agent_api_server_toolsets'],
        hermes_agent_cron_toolsets=defaults['hermes_agent_api_server_toolsets'],
        hermes_agent_slack_toolsets=defaults['hermes_agent_api_server_toolsets'] + ['clarify'],
        hermes_agent_kanban_orchestrator_profile=defaults['hermes_agent_kanban_orchestrator_profile'],
        hermes_agent_plugins_enabled=[],
        hermes_agent_kanban_goal_judge_model='fixture-judge',
    )
    return defaults, config


def test_research_off_keeps_the_client_lockdown():
    defaults, config = _config('pepper', False)
    assert defaults['hermes_agent_pepper_research_enabled'] is False
    assert config['memory'] == {'memory_enabled': False, 'user_profile_enabled': False}
    assert config['platform_toolsets'] == {
        platform: ['kanban'] for platform in ['cli', 'cron', 'api_server', 'slack']}
    assert config['agent']['disabled_toolsets'] == defaults['hermes_agent_disabled_toolsets'] + CLIENT_DENIED


def test_research_on_opens_research_tools_and_keeps_the_rest_denied():
    defaults, config = _config('pepper', True)
    research = defaults['hermes_agent_pepper_research_toolsets']
    assert config['memory'] == {'memory_enabled': True, 'user_profile_enabled': True}
    assert 'provider' not in config['memory']
    assert config['platform_toolsets'] == {
        'cli': ['kanban'], 'cron': research, 'api_server': ['kanban'], 'slack': research}
    denied = set(config['agent']['disabled_toolsets'])
    assert {'terminal', 'code_execution', 'browser', 'delegation',
            'project', 'desktop_ui', 'catalog'} <= denied
    assert not RESEARCH_LIFTED & denied
    assert not {'terminal', 'code_execution', 'browser', 'delegation'} & set(research)
    assert set(defaults['hermes_agent_disabled_toolsets']) <= denied


@pytest.mark.parametrize('identity', ['hermes', 'hermes-private', 'donna'])
def test_the_switch_changes_nothing_for_other_identities(identity):
    assert _config(identity, True)[1] == _config(identity, False)[1]


def test_research_on_turns_every_client_input_off():
    context = dict(
        hermes_agent_id='pepper', hermes_agent_pepper_research_enabled=True,
        hermes_agent_bao={'YOUTRACK_MCP_URL': 'fixture-url', 'YOUTRACK_MCP_TOKEN': 'fixture-token',
                          'PEPPER_VAULT_REPO_URL': 'fixture-repository'},
        environment={'PEPPER_VAULT_ROLE_ID': 'fixture-role', 'PEPPER_VAULT_SECRET_ID': 'fixture-secret'},
        hermes_agent_pepper_vault_bao_addr='https://secrets.example.test',
    )
    assert resolve('hermes_agent_youtrack_mcp_enabled', context) == 'False'
    for name in ['youtrack_mcp_url', 'youtrack_mcp_token', 'pepper_vault_role_id', 'pepper_vault_secret_id']:
        assert resolve('hermes_agent_' + name, context) == ''
    assert resolve('hermes_agent_pepper_vault_enabled', context) == 'False'
    client = dict(context, hermes_agent_pepper_research_enabled=False)
    assert resolve('hermes_agent_youtrack_mcp_enabled', client) == 'True'
    assert resolve('hermes_agent_youtrack_mcp_token', client) == 'fixture-token'
    assert resolve('hermes_agent_pepper_vault_enabled', client) == 'True'


def test_research_on_disables_both_client_jobs():
    jobs = {job['name']: job for job in role_defaults(ROLE)['hermes_agent_direct_cron_jobs_assistant']
            if job['name'].startswith('pepper-')}
    assert set(jobs) == {'pepper-nightly-intake', 'pepper-weekly-digest'}
    env = NativeEnvironment()
    env.filters['bool'] = bool
    for job in jobs.values():
        wanted = dict(hermes_agent_pepper_intake_enabled=True,
                      hermes_agent_pepper_digest_deliver='slack:fixture')
        assert env.from_string(job['enabled']).render(wanted, hermes_agent_pepper_research_enabled=False) is True
        assert env.from_string(job['enabled']).render(wanted, hermes_agent_pepper_research_enabled=True) is False


def test_proxy_hostnames_follow_the_posture():
    project, research_hosts = yaml.safe_load((ROLE / 'tasks/egress_domains.yml').read_text())[1:3]
    env = NativeEnvironment()
    env.filters['bool'] = bool
    names = lambda research: env.from_string(project['loop']).render(_hermes_agent_egress_research=research)
    assert names(False) == ['hermes_agent_pepper_router_host', 'hermes_agent_pepper_youtrack_host',
                            'hermes_agent_pepper_openbao_host', 'hermes_agent_pepper_vault_host']
    assert names(True) == ['hermes_agent_pepper_router_host', 'hermes_agent_pepper_openbao_host']
    extra = lambda research, inventory: env.from_string(research_hosts['loop']).render(
        _hermes_agent_egress_research=research, hermes_agent_egress_inventory=inventory,
        hermes_agent_pepper_research_egress_hosts=['fallback.example.test'])
    assert extra(False, {'hermes_agent_pepper_research_egress_hosts': ['search.example.test']}) == []
    assert extra(True, {'hermes_agent_pepper_research_egress_hosts': ['search.example.test']}) == [
        'search.example.test']
    assert extra(True, {}) == ['fallback.example.test']
    assert research_hosts['ansible.builtin.include_tasks'] == 'egress_host.yml'
    assert research_hosts['vars']['_hermes_agent_egress_host'] == '{{ item }}'


def test_proxy_play_reads_no_client_endpoint_in_the_research_posture():
    client, skip = yaml.safe_load((ROOT / 'playbooks/tasks/pepper_egress_endpoints.yml').read_text())
    assert client['when'] == 'not (_pepper_egress_research | bool)'
    assert skip['when'] == '_pepper_egress_research | bool'
    assert skip['ansible.builtin.set_fact'] == {'squid_pepper_endpoint_hosts': {}}
    assert 'hermes_agent_pepper_research_enabled' in client['vars']['_pepper_egress_research']
    assert client['vars'] == skip['vars']


def test_the_play_refuses_research_with_any_client_wiring():
    tasks = yaml.safe_load((ROLE / 'tasks/assert_brain_and_bridge.yml').read_text())
    guard = next(t for t in tasks if t['name'] == 'Assert the Pepper research posture and the client wiring never coexist')
    assert guard['when'] == ["(hermes_agent_is_isolated | bool)",
                             'hermes_agent_pepper_research_enabled | bool']
    conditions = guard['ansible.builtin.assert']['that']
    for name in ['hermes_agent_pepper_vault_enabled', 'hermes_agent_youtrack_mcp_enabled',
                 'hermes_agent_youtrack_mcp_url', 'hermes_agent_youtrack_mcp_token',
                 'hermes_agent_pepper_intake_enabled', 'hermes_agent_pepper_digest_deliver']:
        assert any(name in condition for condition in conditions), name
    env = NativeEnvironment()
    env.filters.update(bool=bool, trim=lambda value: str(value).strip())
    clean = dict(hermes_agent_pepper_vault_enabled=False, hermes_agent_youtrack_mcp_enabled=False,
                 hermes_agent_youtrack_mcp_url='', hermes_agent_youtrack_mcp_token='',
                 hermes_agent_pepper_intake_enabled=False, hermes_agent_pepper_digest_deliver='')
    holds = lambda context: all(env.from_string('{{ ' + c + ' }}').render(context) for c in conditions)
    assert holds(clean)
    for name, value in [('hermes_agent_pepper_vault_enabled', True), ('hermes_agent_youtrack_mcp_enabled', True),
                        ('hermes_agent_youtrack_mcp_token', 'fixture-token'),
                        ('hermes_agent_pepper_intake_enabled', True),
                        ('hermes_agent_pepper_digest_deliver', 'slack:fixture')]:
        assert not holds(dict(clean, **{name: value})), name


def test_inventory_selects_research_with_plain_search_hostnames():
    inventory = yaml.safe_load(
        (ROOT / 'inventory/group_vars/hermes_agent_group/95-pepper.yml').read_text())
    assert inventory['hermes_agent_pepper_research_enabled'] is True
    hosts = inventory['hermes_agent_pepper_research_egress_hosts']
    assert hosts
    for host in hosts:
        assert re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+', host)
