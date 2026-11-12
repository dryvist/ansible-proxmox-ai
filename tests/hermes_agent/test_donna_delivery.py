"""Donna's job output goes to her Slack channel when her OpenBao domain names one."""
from _pepper_identity_shared import resolve


def _deliver(identity, bao=None, environment=None):
    context = {
        'hermes_agent_identity': identity,
        'hermes_agent_bao': bao or {},
        'environment': environment or {},
    }
    return (
        resolve('hermes_agent_donna_deliver', context).strip(),
        resolve('hermes_agent_donna_failure_deliver', context).strip(),
    )


def test_donna_posts_to_the_channel_from_her_openbao_domain():
    assert _deliver('donna', bao={'SLACK_DELIVER_CHANNEL': 'C0EXAMPLE1'}) == ('slack:C0EXAMPLE1', 'local')


def test_failures_go_to_the_alerts_channel_never_the_main_one():
    bao = {'SLACK_DELIVER_CHANNEL': 'C0EXAMPLE1', 'SLACK_FAILURE_CHANNEL': 'C0EXAMPLE9'}
    assert _deliver('donna', bao=bao) == ('slack:C0EXAMPLE1', 'slack:C0EXAMPLE9')
    same = {'SLACK_DELIVER_CHANNEL': 'C0EXAMPLE1', 'SLACK_FAILURE_CHANNEL': 'C0EXAMPLE1'}
    assert _deliver('donna', bao=same) == ('slack:C0EXAMPLE1', 'local')


def test_env_fallback_names_the_channel_when_openbao_has_none():
    env = {'SLACK_DONNA_DELIVER_CHANNEL': 'C0EXAMPLE2'}
    assert _deliver('donna', environment=env) == ('slack:C0EXAMPLE2', 'local')


def test_without_a_channel_output_stays_local():
    assert _deliver('donna') == ('local', 'local')


def test_other_identities_never_take_the_donna_channel():
    for identity in ('hermes', 'pepper', 'hermes-private'):
        bao = {'SLACK_DELIVER_CHANNEL': 'C0EXAMPLE1', 'SLACK_FAILURE_CHANNEL': 'C0EXAMPLE9'}
        assert _deliver(identity, bao=bao) == ('local', 'local')
