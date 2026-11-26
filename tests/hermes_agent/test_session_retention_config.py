"""The session template turns on Hermes's native session pruning."""

from pathlib import Path

import jinja2
import yaml
from ansible.plugins.filter.core import to_bool

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/hermes_agent"


def _render(**overrides):
    defaults = yaml.safe_load((ROLE / "defaults/main/20-brain-and-slack.yml").read_text())
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROLE / "templates"),
                             undefined=jinja2.StrictUndefined)
    env.filters["bool"] = to_bool
    return yaml.safe_load(env.get_template("config-session-reset.yaml.j2").render(**{**defaults, **overrides}))


def test_defaults_prune_sessions_after_90_days():
    assert _render()["sessions"] == {"auto_prune": True, "retention_days": 90}


def test_overrides_reach_the_rendered_config():
    assert _render(hermes_agent_session_auto_prune="false",
                   hermes_agent_session_retention_days="30")["sessions"] == {
        "auto_prune": False, "retention_days": 30}
