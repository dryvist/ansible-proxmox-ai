"""The checkout-pin task must fetch by an explicit tag refspec.

Without one, ansible.builtin.git may fall back to a bare `git fetch --tags`
that depends on whatever refspec a prior run's clone already stored for this
checkout, rather than the version this run actually requested.
"""

from __future__ import annotations

from pathlib import Path

from _role_files import role_tasks

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"


def test_pin_task_fetches_by_explicit_tag_refspec() -> None:
    tasks = role_tasks(ROLE_ROOT)
    pin_task = next(t for t in tasks if t.get("name") == "Pin the Hermes checkout to the release tag")
    git_args = pin_task["ansible.builtin.git"]
    assert git_args.get("refspec") == (
        "+refs/tags/{{ hermes_agent_version_tag }}:refs/tags/{{ hermes_agent_version_tag }}"
    )
