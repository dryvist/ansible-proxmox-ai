from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment
from _role_files import role_defaults

ROLE_ROOT = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"

BUNDLE_SKILLS = {"dryvist/splunk-monitor", "dryvist/zammad-incidents", "dryvist/github-issues"}
LOCAL_SKILLS = {"hermes_agent/github-issues-api", "research/llm-wiki"}


def test_bundle_skill_copy_only_takes_skills_the_bundle_ships() -> None:
    # Only dryvist/* skills ship in the bundle's skills/ dir; role-local and
    # install-dir skills have their own copy tasks.
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "profiles.yml").read_text())
    task = next(t for t in tasks if t["name"].startswith("Deploy the scoped dryvist skills"))
    takes = Environment().compile_expression(task["when"])
    assert all(takes(hermes_agent_profile_skill=s) for s in BUNDLE_SKILLS)
    assert not any(takes(hermes_agent_profile_skill=s) for s in LOCAL_SKILLS)
    for profile in role_defaults(ROLE_ROOT)["hermes_agent_profiles"]:
        taken = {s for s in profile["skills"] if takes(hermes_agent_profile_skill=s)}
        assert taken <= BUNDLE_SKILLS
