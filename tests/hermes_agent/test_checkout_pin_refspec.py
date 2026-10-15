"""The checkout-pin task must fetch by an explicit tag refspec.

Without one, ansible.builtin.git may fall back to a bare `git fetch --tags`
that depends on whatever refspec a prior run's clone already stored for this
checkout, rather than the version this run actually requested.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from _role_files import hermes_runtime_role_root

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"


def _installer_changed_expression(tasks: list[dict]) -> str:
    compatibility_fact = next(
        task for task in tasks
        if task.get("name") == "Preserve the installer change state for consumer check-mode guards"
    )
    return compatibility_fact["ansible.builtin.set_fact"]["hermes_agent_install"]["changed"]


def _render_installer_changed(expression: str, binary_exists: bool, checkout: dict) -> bool:
    templar = Templar(loader=DataLoader())
    templar.available_variables = {
        "hermes_agent_existing_binary": {"stat": {"exists": binary_exists}},
        "hermes_runtime_checkout": checkout,
    }
    rendered = templar.template(trust_as_template(expression))
    return yaml.safe_load(rendered) if isinstance(rendered, str) else rendered


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

    changed = _installer_changed_expression(consumer_tasks)
    assert "not hermes_agent_existing_binary.stat.exists" in changed
    assert "hermes_runtime_checkout is changed" in changed


@pytest.mark.parametrize(
    ("binary_exists", "checkout", "expected"),
    [
        pytest.param(True, {"changed": True, "before": "old", "after": "new"}, True, id="checkout-moved"),
        pytest.param(True, {"changed": False, "before": "same", "after": "same"}, False, id="checkout-unchanged"),
        pytest.param(
            True,
            {"changed": False, "skipped": True, "skip_reason": "Conditional result was False"},
            False,
            id="checkout-skipped",
        ),
        pytest.param(
            False,
            {"changed": False, "skipped": True, "skip_reason": "Conditional result was False"},
            True,
            id="binary-missing",
        ),
    ],
)
def test_installer_changed_fact_renders_with_checkout_result(
    binary_exists: bool, checkout: dict, expected: bool
) -> None:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "main.yml").read_text())
    expression = _installer_changed_expression(tasks)

    assert _render_installer_changed(expression, binary_exists, checkout) is expected
