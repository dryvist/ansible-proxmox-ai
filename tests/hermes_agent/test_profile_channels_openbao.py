"""Profile Slack channels resolve roster, then OpenBao, then the run environment."""
import json
from pathlib import Path

from jinja2 import Environment

from _role_files import role_defaults

ROLE_ROOT = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
BAO = {"splunk-admin": "C0EXAMPLEA", "homelab-admin": "C0EXAMPLEB"}
ENV = {"splunk-admin": "C0EXAMPLEC"}


def _channels(bao=None, env=None, roster=None):
    expr = role_defaults(ROLE_ROOT)["hermes_agent_slack_profile_channels"]
    jenv = Environment()
    jenv.filters["from_json"] = json.loads
    jenv.globals["lookup"] = lambda kind, name: (env or {}).get(name, "")
    ctx = {"ai_agents": roster or {}, "hermes_agent_id": "hermes", "hermes_agent_env_prefix": "HERMES"}
    if bao is not None:
        ctx["hermes_agent_bao"] = bao
    return jenv.from_string(expr).render(**ctx).strip()


def test_openbao_map_is_used_when_the_roster_has_none():
    assert _channels(bao={"SLACK_PROFILE_CHANNELS": json.dumps(BAO)}) == str(BAO)


def test_openbao_map_wins_over_the_run_environment():
    env = {"SLACK_HERMES_PROFILE_CHANNELS": json.dumps(ENV)}
    assert _channels(bao={"SLACK_PROFILE_CHANNELS": json.dumps(BAO)}, env=env) == str(BAO)


def test_environment_still_applies_without_an_openbao_value():
    env = {"SLACK_HERMES_PROFILE_CHANNELS": json.dumps(ENV)}
    assert _channels(bao={}, env=env) == str(ENV)
    assert _channels(env=env) == str(ENV)


def test_roster_wins_and_empty_sources_give_no_routes():
    roster = {"hermes": {"slack": {"profile_channels": ENV}}}
    assert _channels(bao={"SLACK_PROFILE_CHANNELS": json.dumps(BAO)}, roster=roster) == str(ENV)
    assert _channels() == "{}"
