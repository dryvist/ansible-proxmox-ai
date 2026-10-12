"""The checkout-pin task must fetch by an explicit tag refspec.

Without one, ansible.builtin.git may fall back to a bare `git fetch --tags`
that depends on whatever refspec a prior run's clone already stored for this
checkout, rather than the version this run actually requested.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from _role_files import hermes_runtime_role_root

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"


def test_pin_task_fetches_by_explicit_tag_refspec() -> None:
    tasks = yaml.safe_load((hermes_runtime_role_root() / "tasks" / "install.yml").read_text())
    pin_task = next(
        task for task in tasks
        if task.get("name") == "Pin the Hermes checkout to the requested release tag"
    )
    git_args = pin_task["ansible.builtin.git"]
    assert git_args.get("refspec") == (
        "+refs/tags/{{ hermes_runtime_version_tag }}:refs/tags/{{ hermes_runtime_version_tag }}"
    )

    consumer_tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "main.yml").read_text())
    install_include = next(task for task in consumer_tasks if task.get("name") == "Install the pinned Hermes runtime")
    assert install_include["ansible.builtin.include_role"]["tasks_from"] == "install"
    assert install_include["vars"]["hermes_runtime_version_tag"] == "{{ hermes_agent_version_tag }}"

    compatibility_fact = next(
        task for task in consumer_tasks
        if task.get("name") == "Preserve the installer change state for consumer check-mode guards"
    )
    changed = compatibility_fact["ansible.builtin.set_fact"]["hermes_agent_install"]["changed"]
    assert "not hermes_agent_existing_binary.stat.exists" in changed
    assert "hermes_runtime_checkout.before != hermes_runtime_checkout.after" in changed
