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


def _include_roles(node):
    if isinstance(node, dict):
        if "ansible.builtin.include_role" in node:
            yield node["ansible.builtin.include_role"]
        for value in node.values():
            yield from _include_roles(value)
    elif isinstance(node, list):
        for item in node:
            yield from _include_roles(item)


@pytest.mark.parametrize(
    "playbook",
    ["playbooks/llm-model-store-seed.yml", "playbooks/llm-model-campaign-target.yml"],
)
def test_model_store_callers_use_the_maintained_guest_role(playbook: str) -> None:
    document = yaml.safe_load((REPO_ROOT / playbook).read_text(encoding="utf-8"))
    store_includes = [
        include
        for include in _include_roles(document)
        if include.get("tasks_from") in {"cache-sync.yml", "verify-model-store-origin-repo.yml"}
    ]

    assert store_includes, playbook
    assert {include["name"] for include in store_includes} == {"nvidia_gpu_guest"}
