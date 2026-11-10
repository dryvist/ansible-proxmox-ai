"""The llm-wiki copy tasks read the skill from where the pinned Hermes release ships it."""
from pathlib import Path

TASKS = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent" / "tasks"
# Hermes 0.23.0 moved llm-wiki from skills/research/ to optional-skills/research/.
CURRENT = "{{ hermes_agent_install_dir }}/optional-skills/research/llm-wiki"
OLD = "{{ hermes_agent_install_dir }}/skills/research/llm-wiki"


def test_both_llm_wiki_copy_tasks_use_the_shipped_path():
    for name in ("skills_and_profiles.yml", "profiles.yml"):
        text = (TASKS / name).read_text()
        assert CURRENT in text, name
        assert OLD not in text, name
