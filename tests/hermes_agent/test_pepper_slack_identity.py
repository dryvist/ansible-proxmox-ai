"""Regression coverage for identity-scoped Slack credential views."""
import pytest
import yaml
from jinja2 import Environment
from jinja2.nativetypes import NativeEnvironment

from _pepper_identity_shared import ROLE, resolve
from _role_files import role_defaults


@pytest.mark.parametrize(('identity', 'prefix'), [('pepper', 'PEPPER'), ('donna', 'DONNA')])
def test_pepper_and_donna_read_shared_slack_fields_from_their_identity_view(identity, prefix):
    context = dict(
        hermes_agent_id=identity,
        hermes_agent_identity=identity,
        hermes_agent_env_prefix=prefix,
        hermes_agent_bao={
            'SLACK_BOT_TOKEN': f'{identity}-identity-bot',
            'SLACK_APP_TOKEN': f'{identity}-identity-app',
            f'SLACK_{prefix}_BOT_TOKEN': 'stale-prefixed-bot',
            f'SLACK_{prefix}_APP_TOKEN': 'stale-prefixed-app',
        },
        environment={
            f'SLACK_{prefix}_BOT_TOKEN': f'{identity}-environment-bot',
            f'SLACK_{prefix}_APP_TOKEN': f'{identity}-environment-app',
        },
    )

    assert resolve('hermes_agent_slack_bot_token', context) == f'{identity}-identity-bot'
    assert resolve('hermes_agent_slack_app_token', context) == f'{identity}-identity-app'

    context['hermes_agent_bao'] = {}
    assert resolve('hermes_agent_slack_bot_token', context) == f'{identity}-environment-bot'
    assert resolve('hermes_agent_slack_app_token', context) == f'{identity}-environment-app'


def test_other_slack_identities_keep_prefixed_identity_view_fields():
    context = dict(
        hermes_agent_id='hermes-private',
        hermes_agent_identity='hermes-private',
        hermes_agent_env_prefix='HERMES_PRIVATE',
        hermes_agent_bao={
            'SLACK_BOT_TOKEN': 'generic-bot',
            'SLACK_APP_TOKEN': 'generic-app',
            'SLACK_HERMES_PRIVATE_BOT_TOKEN': 'private-bot',
            'SLACK_HERMES_PRIVATE_APP_TOKEN': 'private-app',
        },
        environment={},
    )

    assert resolve('hermes_agent_slack_bot_token', context) == 'private-bot'
    assert resolve('hermes_agent_slack_app_token', context) == 'private-app'


def test_pepper_digest_accepts_only_its_declared_destinations_and_empty_disables():
    defaults = role_defaults(ROLE)
    context = dict(
        hermes_agent_id='pepper',
        hermes_agent_identity='pepper',
        hermes_agent_env_prefix='PEPPER',
        ai_agents={'pepper': {'slack': {'home_channel': 'pepper-home-fixture'}}},
        environment={},
    )
    home_channel = resolve('hermes_agent_slack_home_channel', context)
    allowed_destinations = NativeEnvironment().from_string(
        defaults['hermes_agent_pepper_digest_allowed_destinations']
    ).render(hermes_agent_slack_home_channel=f' {home_channel} ')
    assert allowed_destinations == ['local', f'slack:{home_channel}']

    tasks = yaml.safe_load((ROLE / 'tasks/cron_reconcile.yml').read_text())
    guard = next(task for task in tasks if task['name'] == 'Assert Pepper owns only its declared jobs')
    accepts_destination = Environment().compile_expression(
        guard['ansible.builtin.assert']['that'][-1]
    )
    cron = next(
        job for job in defaults['hermes_agent_direct_cron_jobs_assistant']
        if job['name'] == 'pepper-weekly-digest'
    )
    enabled = NativeEnvironment().from_string(cron['enabled'])

    def accepted(deliver, bot_token='fixture-bot-token', app_token='fixture-app-token'):
        return accepts_destination(
            hermes_agent_pepper_digest_deliver=deliver,
            hermes_agent_pepper_digest_allowed_destinations=allowed_destinations,
            hermes_agent_slack_bot_token=bot_token,
            hermes_agent_slack_app_token=app_token,
        )

    assert accepted(f'slack:{home_channel}')
    assert accepted('local', bot_token='', app_token='')
    assert not accepted('slack:foreign-channel-fixture')
    assert not accepted(f'slack:{home_channel}', bot_token='', app_token='')
    assert accepted('')
    assert enabled.render(hermes_agent_pepper_digest_deliver='') is False
    assert enabled.render(hermes_agent_pepper_digest_deliver=f'slack:{home_channel}') is True
