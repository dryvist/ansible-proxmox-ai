"""Evaluation seed paths and writers must stay within their grants."""

from __future__ import annotations

import pytest

from _eval_support import GROUP_VARS, ROLES, host_context, load_yaml, render

SIM_EVAL_PATH = load_yaml(ROLES / "sim_docker/defaults/main.yml")["sim_docker_openbao_path"]
GRANTED_EVALUATION_LEAVES = {f"ai/eval/specialist-platform/{leaf}" for leaf in ("sim", "hermes")}


def test_every_evaluation_path_is_a_leaf_the_policy_grants() -> None:
    """After the Dify and LangFlow removal, the ASM converge policy grants exactly two evaluation leaves: sim and hermes. An evaluation path outside them is refused."""
    assert {SIM_EVAL_PATH} <= GRANTED_EVALUATION_LEAVES
    assert len(GRANTED_EVALUATION_LEAVES) == 2


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


@pytest.mark.parametrize("evaluation_path", [SIM_EVAL_PATH])
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


def test_the_sim_writer_uses_no_apps_path() -> None:
    context = host_context("sim_docker")
    app_paths = {
        rendered
        for path in _seed_paths("sim_docker")
        if (rendered := render(path, context)).startswith("apps/")
    }

    assert app_paths == set()
