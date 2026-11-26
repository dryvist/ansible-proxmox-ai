"""Keep the shared work-report fragment wired into the fabric-status prompt.

The fragment body is owned by ai-llm-prompts and its pin is release-gated in
this change. A synthetic catalog response exercises the role's real prompt
composition without pretending the unreleased upstream body is already pinned.

The fabric-status body itself ships with the role (files/), because the
catalog's body led every run into planning a helper program a cron run cannot
execute; the shared fragment still comes from the pinned catalog.
"""

import re
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from _prompt_catalog import prompt_bodies
from _role_files import role_defaults, role_tasks

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE_ROOT)
# The loader strips a leading front matter block before it composes the prompt,
# so the body the worker sees is what the composition has to start with.
FRONT_MATTER = re.compile(r"(?s)^---\n.*?\n---\n")
BASE_PROMPT = FRONT_MATTER.sub(
    "",
    (ROLE_ROOT / "files" / DEFAULTS["hermes_agent_daily_status_cron_prompt_file"]).read_text(),
).strip()


def test_fabric_status_prompt_uses_the_pinned_shared_report_fragment():
    sources = DEFAULTS["hermes_agent_catalog_prompt_sources"]
    summary_source = sources["hermes_agent_daily_status_kanban_summary"]
    assert summary_source["prompt_file"] == "hermes-daily-status-kanban-summary.md"

    base_prompt = BASE_PROMPT

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
    environment.globals["lookup"] = lambda *_args, **_kwargs: base_prompt
    summary_fragment = prompt_bodies(DEFAULTS)[
        "hermes_agent_daily_status_kanban_summary"
    ]
    summary_fragment = environment.from_string(summary_fragment).render(
        hermes_agent_dashboard_port=8123
    ).strip()
    context = {
        **DEFAULTS,
        "hermes_agent_prompts_path": "/fixture/catalog",
        "hermes_agent_daily_status_cron_prompt_file": "hermes-daily-fabric-status.md",
        "hermes_agent_daily_status_endpoints": "LIVE FABRIC ENDPOINTS",
        "hermes_agent_daily_status_kanban_summary": summary_fragment,
    }
    rendered = environment.from_string(expression).render(**context)

    assert rendered.startswith(base_prompt)
    assert rendered.index("LIVE FABRIC ENDPOINTS") < rendered.index(summary_fragment)
    assert rendered.endswith(summary_fragment)
