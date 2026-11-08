"""The Slack allowlist joins the shared member list with each identity's own additions."""
import pytest

from _pepper_identity_shared import resolve


def _allowed(identity, prefix, environment):
    return resolve('hermes_agent_slack_allowed_users', dict(
        hermes_agent_id=identity,
        hermes_agent_identity=identity,
        hermes_agent_env_prefix=prefix,
        environment=environment,
    ))


@pytest.mark.parametrize(('identity', 'prefix'), [('hermes', 'HERMES'), ('donna', 'DONNA'), ('pepper', 'PEPPER')])
def test_every_identity_allows_the_shared_member_list(identity, prefix):
    assert _allowed(identity, prefix, {'SLACK_MEMBER_ID': 'U_SHARED'}) == 'U_SHARED'


def test_identity_members_add_to_the_shared_list_without_duplicates():
    environment = {'SLACK_MEMBER_ID': 'U_SHARED, U_TWO', 'SLACK_DONNA_MEMBER_ID': 'U_DONNA,U_SHARED'}
    assert _allowed('donna', 'DONNA', environment) == 'U_SHARED,U_TWO,U_DONNA'
    assert _allowed('hermes', 'HERMES', environment) == 'U_SHARED,U_TWO'


def test_no_members_renders_an_empty_list():
    assert _allowed('donna', 'DONNA', {}) == ''
