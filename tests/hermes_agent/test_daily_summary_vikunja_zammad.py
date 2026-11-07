"""Checks the rendered daily-summary project list and ranking rules."""
import re
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from _prompt_catalog import prompt_bodies
from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"

PROJECT_LIST = (
    "40 (Inbox), 42 (nix), 43 (proxmox), 44 (governance-ci), 45 (observability), "
    "46 (network), 48 (ai), 49 (hardware), 50 (docs), 71 (secrets-security)"
)
PROJECT_IDS = ("40", "42", "43", "44", "45", "46", "48", "49", "50", "71")


def _prompt(defaults: dict) -> str:
    source = prompt_bodies(defaults)["hermes_agent_summary_cron_prompt"]
    environment = Environment(undefined=StrictUndefined, autoescape=False)
    source_config = defaults["hermes_agent_catalog_prompt_sources"][
        "hermes_agent_summary_cron_prompt"
    ]
    render_inputs = {
        key: environment.from_string(value).render(**defaults)
        for key, value in source_config.get("template_vars", {}).items()
    }
    return environment.from_string(source).render(**defaults, **render_inputs).strip()


def test_vikunja_project_list_is_exact_and_excludes_project_9() -> None:
    defaults = role_defaults(ROLE_ROOT)
    source = prompt_bodies(defaults)["hermes_agent_summary_cron_prompt"]
    source_config = defaults["hermes_agent_catalog_prompt_sources"][
        "hermes_agent_summary_cron_prompt"
    ]
    assert defaults["hermes_agent_vikunja_project_list"] == PROJECT_LIST
    assert source_config["template_vars"] == {
        "vikunja_project_list": "{{ hermes_agent_vikunja_project_list }}"
    }
    assert source.count("{{ vikunja_project_list }}") == 1

    rendered = _prompt(defaults)
    assert rendered == source.replace("{{ vikunja_project_list }}", PROJECT_LIST).strip()
    normalized = " ".join(rendered.split())
    match = re.search(
        r"Query EXACTLY these project ids via the vikunja MCP route, and no others: "
        r"(.*?)\. Do not guess",
        normalized,
    )
    assert match is not None
    ids = re.findall(r"([0-9]+) \(", match.group(1))
    assert ids == list(PROJECT_IDS)
    assert "9" not in ids


def test_zammad_is_ranked_alongside_vikunja_not_a_separate_list() -> None:
    prompt = " ".join(_prompt(role_defaults(ROLE_ROOT)).split())
    assert "SAME top 5-10 ranked list" in prompt or "same top 5-10" in prompt.lower()


def test_daily_summary_never_closes_only_proposes() -> None:
    prompt = " ".join(_prompt(role_defaults(ROLE_ROOT)).split())
    assert "PROPOSE, NEVER CLOSE" in prompt or "propose only" in prompt.lower()
    assert "confirm to close" in prompt
    assert "Never call a resolve" in prompt or "never call a resolve" in prompt.lower()


def test_vikunja_mcp_client_is_enabled_and_gateway_dependency_is_documented() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert defaults["hermes_agent_vikunja_mcp_enabled"] is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
