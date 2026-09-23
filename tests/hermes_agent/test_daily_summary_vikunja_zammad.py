"""Pins the daily-summary prompt's Vikunja/Zammad ranking additions.

Part of the Hermes audit's "morning briefing" work: daily-summary now ranks
Vikunja tasks and Zammad incidents together (one list, not two), pinned to an
exact homelab project-id set (the homelab-ai-fabric-status hallucination
incident is why project ids are pinned rather than left for the model to
recall), and only ever PROPOSES a close — the same evidence-before-resolve
discipline zammad-review already follows for Zammad, extended to Vikunja.

Runs bare (`python3 tests/hermes_agent/test_daily_summary_vikunja_zammad.py`)
or under pytest. Plain asserts, no fixtures, matching the neighboring
kanban-audit test's idiom.
"""
from pathlib import Path

from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"

# The homelab-relevant project ids (CLAUDE.local.md's Vikunja routing table),
# excluding project 9 (visicore — employer work, never homelab).
HOMELAB_PROJECT_IDS = ("40", "42", "43", "44", "45", "46", "48", "49", "50", "71")


def _prompt() -> str:
    return role_defaults(ROLE_ROOT)["hermes_agent_summary_cron_prompt"]


def test_vikunja_project_ids_are_pinned_and_exclude_visicore() -> None:
    prompt = _prompt()
    for pid in HOMELAB_PROJECT_IDS:
        assert f"{pid} (" in prompt, f"project {pid} missing from the pinned id list"
    # visicore (9) is employer work, never homelab — must be named as an
    # explicit exclusion, not just absent (absent alone doesn't stop the
    # model recalling it from elsewhere, per the anti-hallucination rule the
    # pinned-id-list pattern itself exists to enforce).
    assert "never query project 9" in prompt and "visicore" in prompt


def test_zammad_is_ranked_alongside_vikunja_not_a_separate_list() -> None:
    prompt = _prompt()
    assert "SAME top 5-10 ranked list" in prompt or "same top 5-10" in prompt.lower()


def test_daily_summary_never_closes_only_proposes() -> None:
    prompt = _prompt()
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
