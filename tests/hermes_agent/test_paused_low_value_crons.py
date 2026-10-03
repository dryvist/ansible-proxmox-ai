"""Contract checks: ai-news, daily-innovation, pricing-diff and app-seeding
are paused (disable-don't-delete) by default, each via its own capability
flag rather than the bare `*cron_slack_gate` anchor the first three used to
share — so pausing one can never accidentally re-enable a sibling.
"""
from pathlib import Path

from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)

PAUSED_FLAGS = (
    "hermes_agent_ai_news_enabled",
    "hermes_agent_daily_innovation_enabled",
    "hermes_agent_app_seeding_enabled",
    "hermes_agent_pricing_diff_enabled",
)


def test_every_paused_flag_defaults_false():
    for flag in PAUSED_FLAGS:
        assert DEFAULTS[flag] is False, f"{flag} must default to false (paused)"


def _job(name_var):
    wanted = "{{ " + name_var + " }}"
    for job in DEFAULTS["hermes_agent_direct_cron_jobs"]:
        if job["name"] == wanted:
            return job
    raise AssertionError(f"no catalog entry has name {wanted!r}")


def test_ai_news_gate_references_its_own_flag():
    job = _job("hermes_agent_ai_news_cron_name")
    assert "hermes_agent_ai_news_enabled" in job["enabled"]


def test_daily_innovation_gate_references_its_own_flag():
    job = _job("hermes_agent_daily_innovation_cron_name")
    assert "hermes_agent_daily_innovation_enabled" in job["enabled"]


def test_app_seeding_gate_references_its_own_flag():
    job = _job("hermes_agent_app_seeding_cron_name")
    assert "hermes_agent_app_seeding_enabled" in job["enabled"]


def test_pricing_diff_gate_still_references_its_own_flag():
    job = _job("hermes_agent_pricing_diff_cron_name")
    assert "hermes_agent_pricing_diff_enabled" in job["enabled"]
