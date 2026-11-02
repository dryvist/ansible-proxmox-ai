"""Evaluation seed paths and writers must stay within their grants."""

from __future__ import annotations

import pytest

from _eval_support import (
    DIFY_EVAL_PATH,
    GROUP_VARS,
    LANGFLOW_EVAL_PATH,
    PRODUCTION,
    PRODUCTION_PREFETCH,
    ROLES,
    group_vars,
    host_context,
    load_yaml,
    render,
    task_named,
)

DIFY_TASK_FILE = "storage_and_secret.yml"
DIFY_SEED = "Seed the Dify SECRET_KEY through the shared seed_generated task"
LANGFLOW_SEED = "Seed the LangFlow secrets through the shared seed_generated task"
PRODUCTION_WRITER_ENV = "OPENBAO_SEED_WRITER_TOKEN"

SEED_WRITER_TARGETS = [
    pytest.param("dify_docker", "dify_eval_group", DIFY_TASK_FILE, DIFY_SEED, id="dify"),
    pytest.param("langflow_docker", "langflow_eval_group", "main.yml", LANGFLOW_SEED, id="langflow"),
]


@pytest.mark.parametrize(("role", "group", "task_file", "seed_name"), SEED_WRITER_TARGETS)
def test_seed_uses_the_standard_converge_writer(
    role: str, group: str, task_file: str, seed_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seed writes with the converge writer sim_docker uses. No evaluation group overrides it, so the
    ansible-converge policy is the write boundary, and the role's own path (checked above) limits the target."""
    monkeypatch.setenv(PRODUCTION_WRITER_ENV, PRODUCTION)
    context = host_context(role, group, PRODUCTION_PREFETCH)
    seed_vars = task_named(role, task_file, seed_name)["block"][0]["vars"]

    assert "openbao_secrets_converge_writer_token" not in group_vars(group)
    assert render(seed_vars["openbao_secrets_seed_token"], context) == PRODUCTION


GRANTED_EVALUATION_LEAVES = {f"ai/eval/specialist-platform/{leaf}" for leaf in ("sim", "langflow", "dify", "hermes")}


def test_every_evaluation_path_is_a_leaf_the_policy_grants() -> None:
    """The ASM converge policy grants exactly these four leaves. An evaluation path outside them is refused."""
    sim_path = load_yaml(ROLES / "sim_docker/defaults/main.yml")["sim_docker_openbao_path"]

    assert {DIFY_EVAL_PATH, LANGFLOW_EVAL_PATH, sim_path} <= GRANTED_EVALUATION_LEAVES
    assert len(GRANTED_EVALUATION_LEAVES) == 4


def _domain_strings() -> set[str]:
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            found.add(node)

    for name in ("10-domains.yml", "10-domains-identities.yml"):
        walk(load_yaml(GROUP_VARS / "all/openbao_secrets" / name))
    return found


def test_production_prefetch_reads_the_production_app_paths() -> None:
    assert {"ai/dify", "ai/langflow"} <= _domain_strings()


@pytest.mark.parametrize("evaluation_path", [DIFY_EVAL_PATH, LANGFLOW_EVAL_PATH])
def test_no_production_domain_path_covers_the_evaluation_namespace(evaluation_path: str) -> None:
    for domain_path in _domain_strings():
        assert domain_path != evaluation_path, domain_path
        assert not evaluation_path.startswith(domain_path + "/"), domain_path


def _seed_paths(role: str) -> list[str]:
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "openbao_secrets_seed_path":
                    found.append(value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for file in sorted((ROLES / role / "tasks").glob("*.yml")):
        walk(load_yaml(file))
    return found


@pytest.mark.parametrize(
    ("role", "group", "expected"),
    [("dify_docker", "dify_eval_group", DIFY_EVAL_PATH), ("langflow_docker", "langflow_eval_group", LANGFLOW_EVAL_PATH)],
)
def test_every_seed_path_in_the_role_renders_to_the_evaluation_path(role: str, group: str, expected: str) -> None:
    paths = _seed_paths(role)
    context = host_context(role, group, PRODUCTION_PREFETCH)

    assert paths
    assert all(render(path, context) == expected for path in paths)
