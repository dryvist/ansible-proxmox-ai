"""The inventory loader refuses an evaluation guest that also carries a production or secret-reading tag.

The guard is the loader's own assert task. Ansible's Templar renders its condition against offline container
fixtures, one per forbidden combination, so a change to the forbidden list or the suffix rule fails here.
"""

from __future__ import annotations

import pytest

from _eval_support import ROOT, load_yaml, render

INVENTORY = ROOT / "inventory/load_tofu/add_lxc_host_inventory.yml"
TASKS = load_yaml(INVENTORY)
GUARD_NAME = "Refuse an evaluation guest that also carries a production or secret-reading tag"
GUARD = next(task for task in TASKS if task.get("name") == GUARD_NAME)
FORBIDDEN = {"managed-secrets", "clickhouse", "ai-proxied", "dify", "langflow", "sim", "llm-gpu-classify", "hermes-agent", "hermes-donna"}
EVAL_TAGS = ["dify-eval", "langflow-eval", "sim-eval", "future-eval"]


def passes(tags: list[str]) -> bool:
    """True when the loader accepts a container carrying these tags, using the guard's own variables."""
    variables = {"item": {"key": "fixture-container", "value": {"tags": tags}}, **GUARD["vars"]}
    return all(render("{{ " + condition + " }}", variables) for condition in GUARD["ansible.builtin.assert"]["that"])


def test_the_guard_runs_before_any_host_is_added() -> None:
    names = [task.get("name") for task in TASKS]
    first_add_host = next(i for i, task in enumerate(TASKS) if "ansible.builtin.add_host" in task)

    assert names.index(GUARD_NAME) < first_add_host


def test_the_guard_checks_every_published_container_and_only_when_some_are_published() -> None:
    assert GUARD["loop"] == "{{ tofu_data.containers | dict2items }}"
    assert GUARD["when"] == ["tofu_data.containers is defined", "tofu_data.containers | length > 0"]


def test_the_forbidden_list_is_the_production_and_secret_reading_set() -> None:
    assert set(GUARD["vars"]["_eval_guard_forbidden_tags"]) == FORBIDDEN


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN))
@pytest.mark.parametrize("eval_tag", EVAL_TAGS)
def test_an_evaluation_tag_with_a_forbidden_tag_is_refused(eval_tag: str, forbidden: str) -> None:
    assert passes([eval_tag, forbidden]) is False


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN))
@pytest.mark.parametrize("eval_tag", EVAL_TAGS)
def test_the_refusal_does_not_depend_on_tag_order(eval_tag: str, forbidden: str) -> None:
    assert passes([forbidden, eval_tag]) is False


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN))
def test_a_forbidden_tag_is_refused_alongside_other_tags(forbidden: str) -> None:
    assert passes(["dify-eval", "ollama", forbidden]) is False


@pytest.mark.parametrize(
    "tags",
    [
        pytest.param([], id="no-tags"),
        pytest.param(["dify"], id="dify-production"),
        pytest.param(["langflow"], id="langflow-production"),
        pytest.param(["sim"], id="sim-production"),
        pytest.param(["managed-secrets"], id="managed-secrets-production"),
        pytest.param(["clickhouse"], id="clickhouse-production"),
        pytest.param(["ai-proxied"], id="ai-proxied-production"),
        pytest.param(["dify-eval"], id="dify-eval-alone"),
        pytest.param(["langflow-eval"], id="langflow-eval-alone"),
        pytest.param(["sim-eval"], id="sim-eval-alone"),
        pytest.param(["future-eval"], id="future-eval-alone"),
        pytest.param(["dify-eval", "langflow-eval"], id="two-eval-tags"),
        pytest.param(["dify-eval", "ollama"], id="eval-with-unrelated-tag"),
    ],
)
def test_allowed_tag_sets_are_accepted(tags: list[str]) -> None:
    assert passes(tags) is True
