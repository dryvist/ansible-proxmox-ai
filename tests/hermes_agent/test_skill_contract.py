from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = (REPO_ROOT / "roles/hermes_agent/tasks/main.yml").read_text()


def test_zammad_skill_has_one_canonical_deployed_path() -> None:
    assert 'path: "{{ hermes_agent_hermes_home }}/skills/research/dryvist-zammad-incidents"' in TASKS
    assert "state: absent" in TASKS
