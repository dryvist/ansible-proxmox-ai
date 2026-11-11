"""Offline Pepper boundary regressions using the shipped inventory expressions."""
import json

import pytest
import yaml
from jinja2 import Environment, StrictUndefined
from jinja2.nativetypes import NativeEnvironment

from _pepper_identity_shared import ROOT, ROLE, resolve
from _role_files import role_defaults
from test_context_limits import _render

def test_pepper_bundle_is_release_and_commit_pinned():
    import re
    from urllib.parse import parse_qs, urlsplit

    ref = resolve('hermes_agent_bundle_flake_ref', dict(hermes_agent_id='pepper'))
    source = urlsplit(ref)
    query = parse_qs(source.query)
    assert source.path == 'dryvist/nix-hermes'
    assert source.fragment == 'pepper-bundle'
    assert re.fullmatch(r'nix-hermes-v\d+\.\d+\.\d+', query['ref'][0])
    assert re.fullmatch(r'[0-9a-f]{40}', query['rev'][0])
    assert resolve('hermes_agent_bundle_flake_ref', dict(hermes_agent_id='hermes')).endswith('#hermes-bundle')


def test_polluted_controller_cannot_supply_shared_credentials():
    context = dict(hermes_agent_identity='pepper', hermes_agent_id='pepper',
                   hermes_agent_env_prefix='PEPPER', hermes_agent_bao={},
                   bao_local_llm_secrets={'CONTEXT7_API_KEY': 'poison'},
                   environment={key: 'poison' for key in [
                       'WEBHOOK_SECRET', 'FIRECRAWL_API_KEY', 'CONTEXT7_API_KEY',
                       'ZAMMAD_API_TOKEN', 'HERMES_GITHUB_PUBLIC_VAULT_ROLE_ID',
                       'HERMES_AGENT_MODEL_API_KEY']})
    for field in ['webhook_secret', 'firecrawl_api_key', 'context7_api_key',
                  'zammad_api_token', 'zammad_url']:
        assert resolve('hermes_agent_' + field, context) == ''
    assert resolve('hermes_agent_model_api_key', dict(context, bao_apps_secrets={
        'hermes_default_llm_router_key': 'poison'})) == ''
    context['hermes_agent_bao'] = {'pepper_llm_router_key': 'own'}
    assert resolve('hermes_agent_model_api_key', context) == 'own'


def test_youtrack_rendering_is_pepper_only_and_uses_environment_substitution():
    src = (ROLE / 'templates/config.yaml.j2').read_text()
    src = src[src.index('{% set _mcp_youtrack'):src.index("timezone:")]
    env = Environment(undefined=StrictUndefined)
    env.filters.update(bool=bool, to_json=json.dumps)
    context = {f'hermes_agent_{name}_mcp_enabled': False for name in [
        'splunk', 'context7', 'docs', 'grep', 'vikunja', 'nautobot']}
    context.update(hermes_agent_splunk_access=False, hermes_agent_youtrack_mcp_enabled=True,
                   hermes_agent_youtrack_mcp_url='fixture-url', hermes_agent_youtrack_mcp_token='fixture-token',
                   hermes_agent_youtrack_mcp_tools=['get_issue', 'add_issue_comment'],
                   hermes_agent_mcp_tool_timeout_seconds=180)
    for identity in ['pepper', 'hermes', 'donna']:
        rendered = env.from_string(src).render(context, hermes_agent_id=identity)
        servers = (yaml.safe_load(rendered) or {}).get('mcp_servers', {})
        assert set(servers) == ({'youtrack', 'youtrack_intake'} if identity == 'pepper' else set())
        assert 'fixture-token' not in rendered
        if identity == 'pepper':
            assert servers['youtrack']['headers']['Authorization'] == 'Bearer ${YOUTRACK_MCP_TOKEN}'
            assert servers['youtrack_intake']['tools']['include'] == ['search_issues']
            assert servers['youtrack_intake']['tools']['resources'] is False
            assert servers['youtrack_intake']['tools']['prompts'] is False
            assert servers['youtrack_intake']['resources'] is False
            assert servers['youtrack_intake']['prompts'] is False
    context['hermes_agent_youtrack_mcp_token'] = ''
    assert not yaml.safe_load(env.from_string(src).render(context, hermes_agent_id='pepper'))


def test_pepper_router_policy_and_profile_exclusion():
    defaults = role_defaults(ROOT / 'roles/llm_router')
    targets = next(v for v in defaults.values() if isinstance(v, list) and any(
        isinstance(k, dict) and k.get('alias') == 'pepper' for k in v))
    key = next(k for k in targets if k.get('alias') == 'pepper')
    assert key['allowed_classes'] == ['local']
    assert key['tags'] == ['&zdr', '!free', '!retains', '&local']
    assert key['router_settings']['enable_tag_filtering'] is True
    assert key['object_permission'] == {'mcp_servers': [], 'mcp_access_groups': [], 'agents': []}
    assert 'zdr_only' not in key
    assert "!= 'pepper'" in defaults['_llm_router_hermes_profile_keys']


def test_pepper_cron_ownership_and_schedules_preserve_existing_identities():
    defaults = role_defaults(ROLE)
    jobs = defaults['hermes_agent_direct_cron_jobs_assistant']
    pepper = [j for j in jobs if j['identities'] == ['pepper']]
    assert [(j['name'], j['schedule']) for j in pepper] == [
        ('pepper-nightly-intake', '17 6 * * *'), ('pepper-weekly-digest', '47 7 * * 0')]
    assert all(j['identities'] == ['donna'] for j in jobs if j not in pepper)
    assert defaults['hermes_agent_job_identities_default'] == ['hermes']
    assert 'default(false)' in pepper[0]['enabled']


def test_identity_mapping_and_conflicting_tag_rejection():
    tasks = yaml.safe_load((ROOT / 'inventory/load_tofu/add_lxc_host_inventory.yml').read_text())
    mapping = next(t['ansible.builtin.add_host']['hermes_agent_identity'] for t in tasks
                   if 'ansible.builtin.add_host' in t)
    guards = next(t['ansible.builtin.assert']['that'] for t in yaml.safe_load(
        (ROOT / 'inventory/load_tofu/assert_hermes_identity.yml').read_text())
                  if t['name'] == 'Reject conflicting or unrecognized Hermes identity markers')
    env = Environment(undefined=StrictUndefined)
    env.filters.update(difference=lambda a, b: list(set(a) - set(b)),
                       intersect=lambda a, b: list(set(a) & set(b)))
    tags = ['hermes-agent', 'hermes-pepper']
    context = dict(item={'key': 'fixture', 'value': {'tags': tags}},
                   _ai_agent_by_container={})
    assert env.from_string(mapping).render(context) == 'pepper'
    for markers, valid in [(tags, True), (tags + ['hermes-donna'], False),
                           (['hermes-pepper'], False), (['hermes-agent', 'hermes-unknown'], False)]:
        context['identity_markers'] = markers
        assert all(env.from_string('{{ ' + expr + ' }}').render(context) == 'True'
                   for expr in guards) == valid


def test_both_profile_loaders_exclude_pepper():
    for path in ['roles/hermes_agent/tasks/main.yml', 'playbooks/tasks/load_hermes_profiles.yml']:
        src = (ROOT / path).read_text()
        assert "in ['hermes-private', 'pepper']" in src
    for path in ['playbooks/agents.yml', 'playbooks/recover-hermes-queue.yml']:
        assert 'codex_runner' not in (ROOT / path).read_text()


def test_missing_youtrack_token_fails_the_converge_assertion():
    tasks = yaml.safe_load((ROLE / 'tasks/assert_brain_and_bridge.yml').read_text())
    guard = next(t for t in tasks if t['name'] == 'Assert Pepper YouTrack credentials and tool scope')
    assert guard['no_log'] is True
    assert 'hermes_agent_youtrack_mcp_token | trim | length > 0' in guard['ansible.builtin.assert']['that']


def test_pepper_receives_no_github_approle_inputs_from_polluted_environment():
    group = yaml.safe_load((ROOT / 'inventory/group_vars/hermes_agent_group/90-github-identity.yml').read_text())
    env = Environment(undefined=StrictUndefined)
    env.globals['lookup'] = lambda *args: 'poison'
    context = dict(hermes_agent_identity='pepper', hermes_agent_github_vault_env_prefix='HERMES_')
    for boundary in group['hermes_agent_github_identity_approles'].values():
        for expression in boundary.values():
            assert env.from_string(expression).render(context) == ''
    assert env.from_string(group['hermes_agent_github_identity_sets']).render(context) == '{}'


@pytest.mark.parametrize('identity', ['pepper', 'hermes', 'hermes-private', 'donna'])
def test_rendered_pepper_capabilities_and_existing_identity_behavior(identity):
    defaults = role_defaults(ROLE)
    context = dict(hermes_agent_id=identity, hindsight_service_enabled=True)
    provider = resolve('hermes_agent_memory_provider', context)
    goal_mode = resolve('hermes_agent_kanban_goal_mode', context) == 'True'
    plugins = yaml.safe_load(resolve('hermes_agent_plugins', dict(
        context, hermes_agent_plugins_core=defaults['hermes_agent_plugins_core'],
        hermes_agent_langfuse_enabled=False)))
    _, config = _render(
        'config.yaml.j2', {}, hermes_agent_id=identity,
        hermes_agent_memory_provider=provider, hermes_agent_kanban_goal_mode=goal_mode,
        hermes_agent_api_server_key='fixture', hermes_agent_slack_bot_token='fixture',
        hermes_agent_api_max_concurrent_runs=1,
        hermes_agent_api_server_toolsets=defaults['hermes_agent_api_server_toolsets'],
        hermes_agent_cron_toolsets=defaults['hermes_agent_api_server_toolsets'],
        hermes_agent_slack_toolsets=defaults['hermes_agent_api_server_toolsets'] + ['clarify'],
        hermes_agent_kanban_orchestrator_profile=defaults['hermes_agent_kanban_orchestrator_profile'],
        hermes_agent_plugins_enabled=[p['name'] for p in plugins],
        hermes_agent_kanban_goal_judge_model='fixture-judge',
    )
    if identity == 'pepper':
        assert provider == ''
        assert config['memory'] == {'memory_enabled': False, 'user_profile_enabled': False}
        assert 'hindsight' not in config['plugins']['enabled']
        assert config['platform_toolsets'] == {
            platform: ['kanban'] for platform in ['cli', 'cron', 'api_server', 'slack']}
        assert set(['terminal', 'code_execution', 'web', 'search', 'browser', 'file',
                    'memory', 'skills', 'delegation']).issubset(config['agent']['disabled_toolsets'])
        assert config['kanban']['default_assignee'] == 'default'
        assert config['kanban']['orchestrator_profile'] == 'default'
        assert config['kanban']['dispatch_profiles'] == ['default']
        assert {'project', 'desktop_ui', 'catalog'} <= set(config['agent']['disabled_toolsets'])
        assert goal_mode is False
        assert 'goal_judge' not in config['auxiliary']
    else:
        assert provider == 'hindsight'
        assert config['memory']['provider'] == 'hindsight'
        assert config['memory']['memory_enabled'] is True
        assert config['memory']['user_profile_enabled'] is True
        assert config['plugins']['enabled'] == [p['name'] for p in defaults['hermes_agent_plugins_core']]
        assert config['agent']['disabled_toolsets'] == defaults['hermes_agent_disabled_toolsets']
        assert config['platform_toolsets'] == {
            'cron': defaults['hermes_agent_api_server_toolsets'],
            'api_server': defaults['hermes_agent_api_server_toolsets'],
            'slack': defaults['hermes_agent_api_server_toolsets'] + ['clarify']}
        assert config['kanban']['default_assignee'] == 'orchestrator'
        assert config['kanban']['orchestrator_profile'] == 'orchestrator'
        assert 'dispatch_profiles' not in config['kanban']
        assert goal_mode is True
        assert config['auxiliary']['goal_judge']['model'] == 'fixture-judge'


def test_pepper_memory_requires_no_key_or_bank_even_with_polluted_credentials():
    context = dict(hermes_agent_id='pepper', hermes_agent_identity='pepper',
                   hermes_agent_bao={'HINDSIGHT_API_KEY': 'poison', 'fixture': 'poison'},
                   bao_apps_secrets={}, hermes_agent_memory_gateway_key_field='fixture')
    for field in ['api_key', 'bank_id', 'gateway_key']:
        assert resolve('hermes_agent_memory_' + field, context) == ''
    tasks = yaml.safe_load((ROLE / 'tasks/config_and_content.yml').read_text())
    for name in ['Ensure the hindsight config dir exists',
                 'Deploy the hindsight provider config (mode + embedded-daemon LLM)',
                 'Ensure the Hindsight memory bank exists']:
        when = next(t['when'] for t in tasks if t['name'] == name)
        assert "hermes_agent_memory_provider == 'hindsight'" in when
    guard = (ROLE / 'tasks/assert_brain_and_bridge.yml').read_text()
    assert 'hermes_agent_pepper_memory_authorized' not in guard



def test_pepper_router_key_does_not_add_the_goal_judge_route():
    defaults = role_defaults(ROOT / 'roles/llm_router')
    key = next(k for k in defaults['_llm_router_target_virtual_keys'] if k.get('alias') == 'pepper')
    env = NativeEnvironment(undefined=StrictUndefined)
    env.filters['unique'] = lambda values: list(dict.fromkeys(values))
    models = env.from_string(key['models']).render(
        hermes_brain_model='fixture-brain', llm_router_d1_rungs_by_class={'local': ['fixture-local']},
        llm_router_studio_model_names=['fixture-studio'], hermes_goal_judge_model='fixture-judge')
    assert models == ['fixture-brain', 'fixture-local', 'fixture-studio']
    tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
    environment = next(t['vars']['hermes_runtime_gateway_environment'] for t in tasks
                       if 'hermes_runtime_gateway_environment' in t.get('vars', {}))
    from ansible.plugins.filter.core import dict_to_list_of_dict_key_value_elements
    env.filters['dict2items'] = dict_to_list_of_dict_key_value_elements
    for identity in ['pepper', 'hermes', 'hermes-private', 'donna']:
        entries = env.from_string(environment).render(
            hermes_agent_id=identity, hermes_agent_cron_goal_mode_jobs=['fixture-job'],
            hermes_agent_github_app_slug='fixture', hermes_agent_github_trust_boundary_default='fixture',
            hermes_agent_kanban_goal_max_turns=8, hermes_agent_proxy_environment={})
        jobs = next(entry['value'] for entry in entries if entry['name'] == 'HERMES_CRON_GOAL_JOBS')
        assert jobs == ('' if identity == 'pepper' else 'fixture-job')


def test_pepper_intake_requests_only_bounded_metadata():
    prompt = role_defaults(ROLE)['hermes_agent_pepper_intake_prompt']
    assert 'page size of 10' in prompt
    assert 'only issue keys, State and Priority' in prompt
    assert 'never request summaries, descriptions,\ncomments or any other issue text' in prompt
    assert 'at most 10 new Kanban cards' in prompt
    assert '<issue-key>' in prompt
    assert 'longest unchecked' not in prompt
    source = (ROLE / 'defaults/main/40-assistant-identity-prompts.yml').read_text()
    assert ('# shortcut: search_issues returns issue text and the 10-card cap is prompt-enforced;'
            in source)
    assert 'the intake has only Kanban and YouTrack read tools' in source
    assert 'so add a per-run counter if abuse appears.' in source
    jobs = role_defaults(ROLE)['hermes_agent_direct_cron_jobs_assistant']
    intake = next(j for j in jobs if j['name'] == 'pepper-nightly-intake')
    assert intake['enabled_toolsets'] == ['kanban', 'youtrack_intake']
