"""The research posture's proxy hostnames and its inventory switch."""
import re
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / 'roles/hermes_agent'


def test_proxy_hostnames_follow_the_posture():
    project, research_hosts = yaml.safe_load((ROLE / 'tasks/egress_domains.yml').read_text())[1:3]
    env = NativeEnvironment()
    env.filters['bool'] = bool
    names = lambda research: env.from_string(project['loop']).render(_hermes_agent_egress_research=research)
    assert names(False) == ['hermes_agent_isolated_router_host', 'hermes_agent_isolated_youtrack_host',
                            'hermes_agent_isolated_openbao_host']
    assert names(True) == ['hermes_agent_isolated_router_host', 'hermes_agent_isolated_openbao_host']
    extra = lambda research, inventory: env.from_string(research_hosts['loop']).render(
        _hermes_agent_egress_research=research, hermes_agent_egress_inventory=inventory,
        hermes_agent_research_egress_hosts=['fallback.example.test'])
    assert extra(False, {'hermes_agent_research_egress_hosts': ['search.example.test']}) == []
    assert extra(True, {'hermes_agent_research_egress_hosts': ['search.example.test']}) == [
        'search.example.test']
    assert extra(True, {}) == ['fallback.example.test']
    assert research_hosts['ansible.builtin.include_tasks'] == 'egress_host.yml'
    assert research_hosts['vars']['_hermes_agent_egress_host'] == '{{ item }}'


def test_proxy_play_reads_no_client_endpoint_in_the_research_posture():
    client, skip = yaml.safe_load((ROOT / 'playbooks/tasks/isolated_egress_endpoints.yml').read_text())
    assert client['when'] == '_isolated_egress_clients | length > 0'
    assert skip['when'] == '_isolated_egress_clients | length == 0'
    assert skip['ansible.builtin.set_fact'] == {'squid_isolated_endpoint_hosts_by_identity': {}}
    assert 'hermes_agent_research_enabled' in client['vars']['_isolated_egress_clients']
    assert client['vars'] == skip['vars']


def test_inventory_selects_research_unless_tagged_youtrack_client():
    inventory = yaml.safe_load(
        (ROOT / 'inventory/group_vars/hermes_agent_group/95-isolated.yml').read_text())
    research = NativeEnvironment().from_string(inventory['hermes_agent_research_enabled'])
    assert research.render() is True
    assert research.render(host_tags=['hermes-agent', 'hermes-isolated']) is True
    assert research.render(host_tags=['hermes-agent', 'hermes-isolated', 'youtrack-client']) is False
    hosts = inventory['hermes_agent_research_egress_hosts']
    assert hosts
    for host in hosts:
        assert re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+', host)
