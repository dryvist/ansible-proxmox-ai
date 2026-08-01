"""The news scout produces evidence-backed, interest-tied suggestions — or silence.

Two prior incidents shape this contract. The daily-status card, given checks
but no endpoints, invented addresses and reported a healthy fabric as a total
outage. And the whole `web` toolset was silently non-functional from first
deploy until 2026-07-31: no search backend package, no API key, so the backend
cascade landed on its keyless firecrawl default and every web_search call
failed — while three card prompts said "using the web toolset" as if it
worked. A news card is maximal fabrication bait: nothing in the estate
contradicts an invented headline.

These tests pin the pieces that keep the card honest:

  - the venv actually gets a working, keyless search backend (ddgs), and
    config.yaml pins the backend so the cascade cannot regress;
  - the prompt requires a this-run URL for every item, caps volume, names the
    dedup memory key, and fails loudly when search is down;
  - the prompt does not promise the impossible reaction-reading loop (upstream
    hard-disables the messaging toolset in every cron context);
  - the interest seed exists, is referenced by the prompt, and stays
    public-safe (this repo is public — technology names only, no addresses);
  - actionable items are routed to board triage via kanban_create, never to a
    louder Slack channel (the 4-channel routing decision, PR #274, routes by
    observation path; suggestions are reading material).

Runs bare (`python3 tests/hermes_agent/test_news_scout_contract.py`) or under
pytest.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
MAIN_TASKS = (ROLE / "tasks" / "main.yml").read_text()
CONFIG_TEMPLATE = (ROLE / "templates" / "config.yaml.j2").read_text()

_ENV = Environment(autoescape=False)


def _rendered_prompt() -> str:
    """The card body as the enqueuer renders it (interest seed appended)."""
    return _ENV.from_string(DEFAULTS["hermes_agent_ai_news_cron_prompt"]).render(
        hermes_agent_wiki_path="/var/lib/hermes/wiki",
        hermes_agent_ai_news_interests=DEFAULTS["hermes_agent_ai_news_interests"],
    )


# --- the capability: search must actually exist ------------------------------

def test_the_ddgs_backend_is_installed_into_the_venv() -> None:
    """Without this task the toolset resolves to keyless firecrawl and every
    web_search call fails — measured live in the deployed venv 2026-07-31."""
    assert re.search(r"uv_bin }} pip install\s*\n?\s*--python {{ hermes_agent_venv_python }}\s*\n?\s*ddgs",
                     MAIN_TASKS), "no uv pip install of ddgs into the Hermes venv"


def test_config_pins_the_web_backend_to_ddgs() -> None:
    """Pinned, not cascaded: the cascade's terminal default is a backend this
    deployment has no key for, and it fails only at call time."""
    assert re.search(r"^web:\n  backend: ddgs$", CONFIG_TEMPLATE, re.M), (
        "config.yaml.j2 must pin web.backend to ddgs")


def test_the_converge_asserts_search_availability_before_enabling_cards() -> None:
    """The converge runs the upstream backend resolution in the deployed venv
    and fails if it does not resolve available — ordered before the enqueuer
    reconcile, so a converge with dead search cannot (re-)enable a card that
    depends on it. This gate, not the pause list, is what makes 'web works'
    a precondition for the news scout going live."""
    gate = MAIN_TASKS.find("Assert the configured web search backend resolves as available")
    reconcile = MAIN_TASKS.find("Reconcile the per-workload Kanban enqueuer crons")
    assert gate != -1, "the availability assertion task is missing"
    assert reconcile != -1, "the enqueuer reconcile task is missing"
    assert gate < reconcile, "the assertion must run before cards are reconciled"
    assert "_is_backend_available" in MAIN_TASKS, (
        "the gate must run the real upstream availability check, not a proxy")


# --- the prompt contract -----------------------------------------------------

def test_every_item_needs_a_url_retrieved_this_run() -> None:
    p = _rendered_prompt()
    assert "URL that appeared in a tool result THIS run" in p
    assert "never substitute a remembered or plausible-looking URL" in p


def test_search_failure_ends_the_run_instead_of_being_papered_over() -> None:
    assert "if web_search errors or returns nothing, report that in one line" in _rendered_prompt()


def test_volume_is_hard_capped_with_a_silent_floor() -> None:
    p = _rendered_prompt()
    assert "AT MOST 3 suggestions" in p
    assert "No news cleared the bar this run." in p


def test_dedup_and_learning_use_only_what_a_cron_worker_has() -> None:
    """Upstream hard-disables the messaging toolset in every cron context, so a
    cron worker can `hermes send` but can never READ a channel. The old prompt
    told it to learn from reactions on its past posts — an instruction it could
    only fail or fake. The loop must run on memory recall instead."""
    p = _rendered_prompt()
    assert '"ai-news-last"' in p
    assert "recall from memory what the operator recently worked on" in p
    for impossible in ("reaction", "replies to your PAST posts"):
        assert impossible not in p, f"prompt promises channel-reading: {impossible!r}"


def test_actionable_items_route_to_board_triage_not_a_louder_channel() -> None:
    p = _rendered_prompt()
    assert "kanban_create" in p and "news-<slug>-<UTC-date>" in p
    assert "not another\nSlack channel" in p or "not another Slack channel" in p


# --- the interest seed -------------------------------------------------------

def test_the_seed_is_appended_to_the_card_body() -> None:
    assert "INTEREST SEED" in _rendered_prompt(), (
        "the interests var is not rendered into the prompt")


def test_the_seed_is_public_safe() -> None:
    """This repo is public. The seed is technology names only — an IP literal,
    a lookup of the private subdomain, or an FQDN means topology leaked."""
    seed = DEFAULTS["hermes_agent_ai_news_interests"]
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", seed), "IP literal in the interest seed"
    assert "PROXMOX_SUBDOMAIN" not in seed
    assert not re.search(r"\b[\w-]+\.(jacobpevans|local|lan|internal)\b", seed)


# --- activation and routing --------------------------------------------------

def test_the_news_card_is_the_only_scout_off_the_pause_list() -> None:
    """Operator request 2026-07-31: the news scout runs; daily-innovation stays
    throttled until board capacity is proven (the wedge that created the list)."""
    paused = set(DEFAULTS["hermes_agent_kanban_paused_jobs"])
    assert "{{ hermes_agent_ai_news_cron_name }}" not in paused
    assert "{{ hermes_agent_daily_innovation_cron_name }}" in paused


def test_the_innovation_card_reads_the_scouts_finds() -> None:
    """The operator's ask is suggestions informed by news — the innovation card
    is the proposal vehicle, so it must consult the scout's trail."""
    p = DEFAULTS["hermes_agent_daily_innovation_cron_prompt"]
    assert '"ai-news-last"' in p and "ai-news-interests.md" in p


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
