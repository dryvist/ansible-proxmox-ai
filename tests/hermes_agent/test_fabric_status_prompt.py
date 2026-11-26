"""The fabric-status prompt must finish in one pass, without running anything.

A cron run has no approver, so Hermes refuses every code-execution call there
(the upstream change "no code execution in cron runs", shipped in v2.75.2).
The body this role used to load from the pinned prompt catalog told the worker
to gather the board counts, and the worker planned a helper program of its own
to count them — a plan no cron run can carry out, so every run ended in an
interrupted fragment instead of a report.

The prompt that ships with the role must therefore carry the whole run's
discipline in its own text: the tools the job already grants and nothing else,
a hard tool-call budget, a hard output shape, and a final line even when a
source fails.
"""

import re
from pathlib import Path

import pytest

from _role_files import role_defaults, role_tasks

ROLE_ROOT = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE_ROOT)
PROMPT_FILE = ROLE_ROOT / "files" / DEFAULTS["hermes_agent_daily_status_cron_prompt_file"]
PROMPT = PROMPT_FILE.read_text()

# Any instruction to create, write or run a program of the worker's own is a
# plan a cron run cannot finish. The bare word is refused as well: this prompt
# has no reason to mention one, not even to forbid one — the refusal belongs
# to the run's own words, and a stray mention is how the habit comes back.
FORBIDDEN = (
    r"\.py\b",
    r"\bpython\b",
    r"\bscripts?\b",
    r"execute_code",
    r"heredoc",
)


def test_the_prompt_body_ships_with_the_role():
    assert PROMPT_FILE.is_file(), (
        "the fabric-status prompt must be a file this role owns, so the check "
        "below can read it without reaching into the pinned catalog"
    )
    assert PROMPT.strip()


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_the_prompt_never_asks_the_worker_for_a_program_of_its_own(pattern):
    assert not re.search(pattern, PROMPT, re.IGNORECASE), (
        f"{PROMPT_FILE.name} mentions {pattern!r}. A cron run has no approver, "
        "so Hermes refuses every code-execution call there: a prompt that asks "
        "for one can only end in an interrupted fragment, never a report."
    )


def test_the_prompt_gathers_with_the_tools_the_job_already_grants():
    assert "Use only the tools this job grants you" in PROMPT
    assert "Count what a tool" in PROMPT


def test_the_prompt_caps_the_tool_calls_it_spends():
    assert re.search(r"at most \d+ tool calls", PROMPT)


def test_the_prompt_states_a_hard_output_shape():
    assert "at most 15 lines" in PROMPT
    assert "no Markdown tables" in PROMPT


def test_the_prompt_always_ends_with_a_line_and_names_a_failed_source():
    assert "Never drop the final line" in PROMPT
    assert "unavailable" in PROMPT


def test_the_prompt_keeps_its_purpose_and_its_delta_discipline():
    for kept in (
        "LLM router health",
        "Hermes gateway health",
        "Hindsight memory-pool health",
        "merge-ready pull requests",
        "board counts",
        "fabric-status-last",
    ):
        assert kept in PROMPT, kept


def test_the_role_composes_the_prompt_from_the_file_it_owns():
    expression = next(
        task["ansible.builtin.set_fact"]["hermes_agent_daily_status_cron_prompt"]
        for task in role_tasks(ROLE_ROOT, "prompt_catalog.yml")
        if "hermes_agent_daily_status_cron_prompt"
        in task.get("ansible.builtin.set_fact", {})
    )
    assert "lookup('ansible.builtin.file'" in expression
    assert "hermes_agent_daily_status_cron_prompt_file" in expression
    # The role-owned body still carries the live topology and the shared board
    # fragment, in that order.
    assert expression.index("hermes_agent_daily_status_endpoints") < expression.index(
        "hermes_agent_daily_status_kanban_summary"
    )
