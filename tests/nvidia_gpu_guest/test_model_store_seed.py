"""Exercise model-store seed coverage against the declared artifact catalog."""

from pathlib import Path
import subprocess

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("missing_profile", "expected_rc"), [(None, 0), ("16gb", 2)])
def test_seed_coverage_accepts_the_catalog_and_rejects_a_missing_profile(tmp_path, missing_profile, expected_rc):
    seed = yaml.safe_load((REPO_ROOT / "playbooks/llm-model-store-seed.yml").read_text())[0]
    tasks = seed["pre_tasks"][3:]
    for task in tasks:
        include = task.get("ansible.builtin.include_vars")
        if include:
            include["file"] = include["file"].replace("{{ playbook_dir }}/..", str(REPO_ROOT))
    if missing_profile:
        # Drop the profile from the catalog itself. The declared profile set is
        # not derived from the catalog, so the seed coverage assert must reject it.
        tasks.insert(2, {
            "name": "Remove one profile from the artifact catalog",
            "ansible.builtin.set_fact": {
                "_llm_model_artifacts": (
                    "{{ _llm_model_artifacts | selectattr('model_store_profile', 'defined')"
                    " | rejectattr('model_store_profile', 'equalto', '"
                    + missing_profile + "') | list }}"
                ),
            },
        })

    playbook = tmp_path / "seed-coverage.yml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Verify the real seed coverage tasks without downloads",
        "hosts": "localhost",
        "gather_facts": False,
        "tasks": tasks,
    }], sort_keys=False))
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_rc, result.stdout + result.stderr
    if missing_profile:
        assert "all declared model-store profiles" in result.stdout
