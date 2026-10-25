"""Export the lane-class names owned by this repository."""

from __future__ import annotations

import json
from pathlib import Path

from test_alias_sources import load_yaml_documents
from test_lane_fallback_chains import load_registry


SOURCE = "ansible-proxmox-ai"
ROLE_DEPLOYMENTS = "roles/llm_router/defaults/main/55-roles.yml"


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return set().union(*(_strings(item) for item in value)) if value else set()
    return set()


def _values_for_fields(entries: list[dict[str, object]], fields: tuple[str, ...]) -> set[str]:
    return set().union(*(_strings(entry.get(field)) for entry in entries for field in fields))


def _inventory_model_values(repo_root: Path, excluded: set[str]) -> set[str]:
    names: set[str] = set()
    for path in sorted((repo_root / "inventory").glob("**/*.yml")):
        for document in load_yaml_documents(path):
            if not isinstance(document, dict):
                continue
            for key, value in document.items():
                if (
                    str(key).endswith("_model")
                    and isinstance(value, str)
                    and "{{" not in value
                    and value.strip()
                    and value.strip() not in excluded
                ):
                    names.add(value.strip())
    return names


def lane_alias_names(repo_root: Path) -> list[str]:
    """Return stable aliases, router roles, and literal inventory lane selections."""
    registry = load_registry(repo_root)
    names = _values_for_fields(registry, ("stable_aliases",))
    excluded = _values_for_fields(
        registry,
        (
            "client_model_id",
            "upstream_model_id",
            "route_group",
            "fallback_group",
            "fallback_groups",
            "fallbacks",
        ),
    )

    roles_path = repo_root / ROLE_DEPLOYMENTS
    if roles_path.exists():
        documents = load_yaml_documents(roles_path)
        role_document = documents[0] if documents and isinstance(documents[0], dict) else {}
        deployments = role_document.get("_llm_router_role_deployments_source", [])
        if isinstance(deployments, list):
            names.update(
                str(deployment["role"])
                for deployment in deployments
                if isinstance(deployment, dict) and isinstance(deployment.get("role"), str)
            )

    names.update(_inventory_model_values(repo_root, excluded))
    return sorted(names)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    print(json.dumps({"source": SOURCE, "names": lane_alias_names(repo_root)}, sort_keys=True))


if __name__ == "__main__":
    main()
