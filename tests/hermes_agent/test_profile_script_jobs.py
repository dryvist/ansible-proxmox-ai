"""Profile-homed script jobs get their script copied, and an upgrade forces a restart."""
from pathlib import Path

import yaml
from jinja2 import Environment

from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
HOME = "/var/lib/hermes/.hermes"


def _profile_script_jobs(jobs):
    env = Environment()
    env.tests["truthy"] = bool
    expr = role_defaults(ROLE)["hermes_agent_profile_script_jobs"]
    rendered = env.from_string(expr).render(
        hermes_agent_direct_cron_jobs_owned=jobs, hermes_agent_hermes_home=HOME
    )
    return yaml.safe_load(rendered)


def test_only_script_jobs_outside_the_default_home_are_selected():
    jobs = [
        {"name": "canary", "script": "c.py", "hermes_home": f"{HOME}/profiles/homelab-admin", "enabled": True},
        {"name": "off", "script": "o.py", "hermes_home": f"{HOME}/profiles/splunk-admin", "enabled": False},
        {"name": "default-home", "script": "d.py", "hermes_home": HOME, "enabled": True},
        {"name": "no-home", "script": "n.py", "enabled": True},
        {"name": "agentic", "hermes_home": f"{HOME}/profiles/homelab-admin", "enabled": True},
        {"name": "string-off", "script": "s.py", "hermes_home": f"{HOME}/profiles/homelab-admin", "enabled": "false"},
        {"name": "string-on", "script": "t.py", "hermes_home": f"{HOME}/profiles/homelab-admin", "enabled": "true"},
        {"name": "empty-script", "script": "", "hermes_home": f"{HOME}/profiles/homelab-admin", "enabled": True},
    ]
    assert [job["name"] for job in _profile_script_jobs(jobs)] == ["canary", "off", "string-off", "string-on"]


def test_cron_reconcile_copies_scripts_into_profile_homes_before_creating_jobs():
    tasks = yaml.safe_load((ROLE / "tasks" / "cron_reconcile.yml").read_text())
    names = [task["name"] for task in tasks]
    place = names.index("Place each profile script job's script in that profile's scripts directory")
    copy = tasks[place]["ansible.builtin.copy"]
    assert copy["remote_src"] is True
    assert copy["dest"] == "{{ item.hermes_home }}/scripts/{{ item.script }}"
    assert names.index("Read which profile homes that run a script job exist") < names.index(
        "Create the scripts directory of each profile home that runs a script job"
    ) < place
    assert "not ansible_check_mode" in tasks[place]["when"]
    assert "item.enabled | default(false) | bool" in tasks[place]["when"]
    assert place < names.index("Warn when Slack is not configured (direct-deliver crons skipped)")


def test_gateway_restart_digest_includes_the_pinned_hermes_version():
    tasks = yaml.safe_load((ROLE / "tasks" / "finalize.yml").read_text())
    digest = next(t for t in tasks if t["name"] == "Set the desired gateway input digest")
    assert "'hermes_agent_version=' ~ (hermes_agent_version | default(''))" in digest["ansible.builtin.set_fact"][
        "hermes_agent_gateway_desired_digest"
    ]
