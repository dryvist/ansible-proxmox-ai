from pathlib import Path
from _role_files import role_tasks_text


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = role_tasks_text(REPO_ROOT / "roles" / "hermes_agent")


def test_zammad_skill_has_one_canonical_deployed_path() -> None:
    assert 'path: "{{ hermes_agent_hermes_home }}/skills/research/dryvist-zammad-incidents"' in TASKS
    assert "state: absent" in TASKS
