from pathlib import Path
from _role_files import role_tasks_text


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = role_tasks_text(REPO_ROOT / "roles" / "hermes_agent")


def test_zammad_skill_has_one_canonical_deployed_path() -> None:
    assert 'path: "{{ hermes_agent_hermes_home }}/skills/research/dryvist-zammad-incidents"' in TASKS
    assert "state: absent" in TASKS


def test_browser_use_is_a_terminal_cli_with_a_local_cdp_browser() -> None:
    assert '"browser-use=={{ hermes_agent_browser_use_version }}"' in TASKS
    assert "hermes-browser-use-chromium.service.j2" in TASKS
    assert '"{{ hermes_agent_bundle_path }}/skills/browser-use/"' in TASKS
    assert "BROWSER_USE_API_KEY" not in TASKS
