"""A second hermes-isolated identity gets the isolated posture without Pepper's own rollout checks."""
import yaml

from _pepper_identity_shared import ROLE, resolve
from _role_files import role_defaults

ISOLATED = dict(hermes_agent_id='tracker-x', hermes_agent_identity='tracker-x', hermes_agent_isolated=True,
                hermes_agent_env_prefix='TRACKER_X', hindsight_service_enabled=True, ai_agent_owner={})


def test_a_second_isolated_identity_gets_no_shared_memory_splunk_or_hindsight_plugin():
    context = dict(ISOLATED, bao_apps_secrets={'hindsight_api_key': 'poison'}, hermes_agent_bao={})
    for field in ['provider', 'api_key', 'bank_id']:
        assert resolve('hermes_agent_memory_' + field, context) == ''
    assert str(resolve('hermes_agent_splunk_access', context)) == 'False'
    plugins = yaml.safe_load(resolve('hermes_agent_plugins', dict(
        context, hermes_agent_plugins_core=role_defaults(ROLE)['hermes_agent_plugins_core'],
        hermes_agent_langfuse_enabled=False)))
    assert 'hindsight' not in [plugin['name'] for plugin in plugins]


def test_a_second_isolated_identity_reads_its_router_key_from_its_own_view():
    context = dict(ISOLATED, hermes_agent_bao={'tracker_x_llm_router_key': 'own'},
                   bao_apps_secrets={'tracker_x_llm_router_key': 'poison'})
    assert resolve('hermes_agent_model_api_key', context) == 'own'


def test_only_pepper_carries_the_pepper_rollout_prerequisites():
    tasks = yaml.safe_load((ROLE / 'tasks/assert_brain_and_bridge.yml').read_text())
    boundaries = next(t for t in tasks if t['name'] == 'Assert isolated identity boundaries')
    assert boundaries['when'] == '(hermes_agent_is_isolated | bool)'
    assert not any('pepper' in condition for condition in boundaries['ansible.builtin.assert']['that'])
    rollout = next(t for t in tasks if t['name'] == 'Assert Pepper rollout prerequisites')
    assert "hermes_agent_id == 'pepper'" in rollout['when']
