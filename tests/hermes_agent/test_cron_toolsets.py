"""Cron jobs never get the code tool that Hermes refuses in unattended runs."""
import ast

from _pepper_identity_shared import resolve


def _toolsets(name):
    value = resolve(name, {'hermes_agent_a2a_peers': []})
    return value if isinstance(value, list) else ast.literal_eval(value)


def test_cron_toolsets_drop_only_code_execution():
    api = _toolsets('hermes_agent_api_server_toolsets')
    assert 'code_execution' in api
    assert _toolsets('hermes_agent_cron_toolsets') == [t for t in api if t != 'code_execution']


def test_slack_keeps_code_execution_with_its_approver():
    assert 'code_execution' in _toolsets('hermes_agent_slack_toolsets')
