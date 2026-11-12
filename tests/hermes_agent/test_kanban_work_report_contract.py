"""Keep the shared work-report fragment wired into the fabric-status prompt.

The fragment body is owned by ai-llm-prompts and its pin is release-gated in
this change. A synthetic catalog response exercises the role's real prompt
composition without pretending the unreleased upstream body is already pinned.
"""

import re
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from _role_files import role_defaults, role_tasks

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE_ROOT)
BASE_PROMPT = """---
name: fabric-status
---
BASE FABRIC STATUS REPORT
"""
REPORT_CONTRACT = (
    "Report fetched open counts by priority and what was acted on. For every "
    "urgent item not acted on, include its card ID and exactly one reason: "
    "not permitted for this profile (and identify the owner profile), no valid "
    "assignee, not ready, blocked by a guard, or waiting on a person. Do not "
    'say "nothing new" while an urgent fetched item is unaccounted for.'
)


def test_fabric_status_prompt_carries_the_shared_report_contract_verbatim():
    sources = DEFAULTS["hermes_agent_catalog_prompt_sources"]
    summary_source = sources["hermes_agent_daily_status_kanban_summary"]
    assert summary_source["prompt_file"] == "hermes-daily-status-kanban-summary.md"

    task = next(
        task for task in role_tasks(ROLE_ROOT, "prompt_catalog.yml")
        if "hermes_agent_daily_status_cron_prompt"
        in task.get("ansible.builtin.set_fact", {})
    )
    expression = task["ansible.builtin.set_fact"]["hermes_agent_daily_status_cron_prompt"]
    assert expression.index("hermes_agent_daily_status_endpoints") < expression.index(
        "hermes_agent_daily_status_kanban_summary"
    )

    environment = Environment(undefined=StrictUndefined, autoescape=False)
    environment.filters["regex_replace"] = (
        lambda value, pattern, replacement="": re.sub(pattern, replacement, value)
    )
    environment.globals["lookup"] = lambda *_args, **_kwargs: BASE_PROMPT
    context = {
        **DEFAULTS,
        "hermes_agent_prompts_path": "/fixture/catalog",
        "hermes_agent_daily_status_cron_prompt_file": "hermes-daily-fabric-status.md",
        "hermes_agent_daily_status_endpoints": "LIVE FABRIC ENDPOINTS",
        "hermes_agent_daily_status_kanban_summary": REPORT_CONTRACT,
    }
    rendered = environment.from_string(expression).render(**context)

    assert rendered.startswith("BASE FABRIC STATUS REPORT")
    assert rendered.index("LIVE FABRIC ENDPOINTS") < rendered.index(REPORT_CONTRACT)
    assert rendered.endswith(REPORT_CONTRACT)
