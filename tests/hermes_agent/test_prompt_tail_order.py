"""A job's run-specific tail comes after the stable prompt and the shared footer."""

import re
from pathlib import Path

import yaml
from jinja2 import Environment

from _role_files import direct_plan_var

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"


def _assemble(item: dict) -> str:
    expression = direct_plan_var(ROLE, "hermes_agent_direct_prompt_text")
    environment = Environment(autoescape=False)
    environment.filters["regex_replace"] = lambda value, pattern, replacement="": re.sub(
        pattern, replacement, value
    )
    environment.globals["lookup"] = lambda source, name, **kwargs: (
        "STABLE PROMPT" if source == "vars" else "SHARED FOOTER"
    )
    return environment.from_string(expression).render(
        item=item, hermes_agent_direct_deliver="slack:C0EXAMPLE", hermes_agent_prompts_path="/prompts"
    )


def test_the_tail_is_last_and_the_prefix_is_shared() -> None:
    first = _assemble({"prompt_var": "p", "prompt_tail": "REPORT: one"})
    second = _assemble({"prompt_var": "p", "prompt_tail": "REPORT: two"})
    assert first == "STABLE PROMPT\n\nSHARED FOOTER\n\nREPORT: one"
    assert first.removesuffix("one") == second.removesuffix("two")


def test_a_job_without_a_tail_is_unchanged() -> None:
    assert _assemble({"prompt_var": "p"}) == "STABLE PROMPT\n\nSHARED FOOTER"
