"""Slack reply threading and the cron continuable surface move together.

Hermes only honours cron_continuable_surface=in_channel when
reply_in_thread is false; otherwise it warns and falls back to a thread.
"""

from pathlib import Path

from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"


def test_cron_surface_matches_reply_threading():
    d = role_defaults(ROLE)
    reply_in_thread = d["hermes_agent_slack_reply_in_thread"]
    surface = d["hermes_agent_slack_cron_continuable_surface"]
    assert isinstance(reply_in_thread, bool)
    assert surface in ("thread", "in_channel")
    assert surface == ("thread" if reply_in_thread else "in_channel")
