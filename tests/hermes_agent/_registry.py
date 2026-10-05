"""The model registry as the hermes_agent tests read it.

One loader, shared, so no test carries its own copy of how llm-models.d/ is
read — and no test names a model: a backend is selected by the serving_role
or alias the registry declares for it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_registry() -> list[dict]:
    """Every entry of every slice, in slice order."""
    return [
        entry
        for slice_file in sorted((REPO_ROOT / "llm-models.d").glob("*.yml"))
        for entries in yaml.safe_load(slice_file.read_text()).values()
        for entry in entries
        if "client_model_id" in entry
    ]


def backend_for_role(registry: list[dict], role: str) -> str:
    """client_model_id of the enabled entry carrying this serving_role."""
    return next(
        entry["client_model_id"]
        for entry in registry
        if entry.get("enabled") and entry.get("serving_role") == role
    )


def role_is_parked(registry: list[dict], role: str) -> bool:
    """Whether a role's registry entry is explicitly disabled as standby."""
    return any(
        entry.get("serving_role") == role
        and not entry.get("enabled")
        and entry.get("standby")
        for entry in registry
    )


def effective_backend_for_role(registry: list[dict], role: str) -> str:
    """Runtime target, including the temporary primary fallback for parked routine."""
    active = next(
        (
            entry["client_model_id"]
            for entry in registry
            if entry.get("enabled") and entry.get("serving_role") == role
        ),
        None,
    )
    if active is not None:
        return active
    if role == "routine" and role_is_parked(registry, role):
        return backend_for_role(registry, "primary")
    return backend_for_role(registry, role)


def backend_for_alias(registry: list[dict], alias: str) -> str:
    """client_model_id of the enabled entry declaring this stable alias."""
    return next(
        entry["client_model_id"]
        for entry in registry
        if entry.get("enabled") and alias in entry.get("stable_aliases", [])
    )
