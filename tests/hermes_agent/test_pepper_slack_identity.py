"""Regression coverage for identity-scoped Slack credential views."""
import pytest

from _pepper_identity_shared import resolve


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
