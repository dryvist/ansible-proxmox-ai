"""The app-seeding card gets its endpoints handed to it, never invented.

Third instance of the pattern (after fabric-status and the news/docs scouts):
a card prompt instructing work against services whose addresses the prompt
never names. The fabric-status incident measured what happens next — the
worker invents addresses, every probe returns 000, and a healthy service is
reported broken. This pins the role-side endpoints append before the card is
ever unpaused.

Runs bare (`python3 tests/hermes_agent/test_app_seeding_endpoints.py`) or
under pytest.
"""

import re
from pathlib import Path

from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = role_defaults(REPO_ROOT / "roles" / "hermes_agent")


def test_the_prompt_embeds_the_endpoints_block() -> None:
    assert "{{ hermes_agent_app_seeding_endpoints }}" in (
        DEFAULTS["hermes_agent_app_seeding_cron_prompt"]
    )


def test_the_block_names_both_services_and_forbids_guessing() -> None:
    block = DEFAULTS["hermes_agent_app_seeding_endpoints"]
    assert "https://langflow." in block and "https://langfuse." in block
    assert "EXACTLY THESE" in block
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", block), "IP literal"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
