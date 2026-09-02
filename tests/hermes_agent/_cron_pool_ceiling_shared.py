"""Shared harness for hermes_agent_cron_wall_timeout_seconds' derived formula.

The value is now a Jinja template in defaults/main/20-brain-and-slack.yml that
references hermes_agent_direct_cron_jobs (itself assembled from schedule
strings, some of them one more level of `{{ var }}` indirection away) and the
cron_min_gap_minutes filter (roles/hermes_agent/filter_plugins/cron_schedule.py).

role_defaults() only resolves whole-value list concatenation, not arbitrary
Jinja, so this renders the SAME template text Ansible would, through the SAME
filter, against the real resolved schedule list — rather than reimplementing
the formula's arithmetic in Python, which could stay green while the YAML
itself drifted from it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment

from _role_files import role_defaults

ROLE_ROOT = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROLE_ROOT / "filter_plugins"))
from cron_schedule import cron_min_gap_minutes  # noqa: E402

_VAR_REF = re.compile(r"^\{\{\s*(\w+)\s*\}\}$")


def _resolve_schedule(raw: str, defaults: dict) -> str:
    """One level of `{{ var }}` indirection, or the literal cron string itself
    (both shapes are used across the four job-list files this role merges).
    """
    match = _VAR_REF.match(raw)
    return defaults[match.group(1)] if match else raw


def resolved_defaults() -> dict:
    """role_defaults() with every hermes_agent_direct_cron_jobs entry's
    `schedule` field dereferenced down to its literal cron string.
    """
    defaults = role_defaults(ROLE_ROOT)
    defaults["hermes_agent_direct_cron_jobs"] = [
        {**job, "schedule": _resolve_schedule(job["schedule"], defaults)}
        for job in defaults["hermes_agent_direct_cron_jobs"]
    ]
    return defaults


def router_request_timeout_seconds() -> int:
    """ai_router_request_timeout_seconds, read live from group_vars/all.yml —
    the wall-clock ceiling is capped below it, so tests must read the same
    source rather than pin a copy of the number that could drift from it.
    """
    all_vars = yaml.safe_load(
        (REPO_ROOT / "inventory" / "group_vars" / "all.yml").read_text()
    )
    return int(all_vars["ai_router_request_timeout_seconds"])


def _render(template: str, context: dict) -> str:
    env = Environment(autoescape=False)
    env.filters["cron_min_gap_minutes"] = cron_min_gap_minutes
    return env.from_string(template).render(**context)


def min_job_interval_seconds(defaults: dict | None = None) -> int:
    defaults = defaults if defaults is not None else resolved_defaults()
    return int(
        _render(defaults["hermes_agent_cron_min_job_interval_seconds"], defaults)
    )


def render_wall_timeout_seconds(
    min_interval_seconds: int, safety_factor: float, router_timeout_seconds: int
) -> int:
    """Render the real hermes_agent_cron_wall_timeout_seconds template text
    against synthetic inputs, independent of live schedule data — the only
    way to exercise BOTH branches of its `min()` cap on demand: at today's
    real numbers the router-timeout branch is always the tighter one
    (ai_router_request_timeout_seconds - 1 < the real min job interval), so
    the safety-factor branch never binds in resolved_defaults()/
    wall_timeout_seconds() and a broken factor would go uncaught there.
    """
    template = role_defaults(ROLE_ROOT)["hermes_agent_cron_wall_timeout_seconds"]
    return int(
        _render(
            template,
            {
                "hermes_agent_cron_min_job_interval_seconds": min_interval_seconds,
                "hermes_agent_cron_pool_safety_factor": safety_factor,
                "ai_router_request_timeout_seconds": router_timeout_seconds,
            },
        )
    )


def wall_timeout_seconds(defaults: dict | None = None) -> int:
    """Render the real two-step formula: the min-interval template first
    (its result feeds the ceiling template), exactly as Ansible resolves
    nested lazy templates when hermes_agent_cron_wall_timeout_seconds is
    actually used.
    """
    defaults = defaults if defaults is not None else resolved_defaults()
    context = {
        **defaults,
        "hermes_agent_cron_min_job_interval_seconds": min_job_interval_seconds(
            defaults
        ),
        "ai_router_request_timeout_seconds": router_request_timeout_seconds(),
    }
    return int(_render(defaults["hermes_agent_cron_wall_timeout_seconds"], context))
