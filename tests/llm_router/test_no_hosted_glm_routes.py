"""Hosted Z.ai GLM names cannot enter router routes or model-name lists."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from _registry_retype_scan import _Permissive

REPO_ROOT = Path(__file__).resolve().parents[2]
HOSTED_GLM_NAME = re.compile(r"glm|z-ai/|zai-", re.IGNORECASE)
NAME_LIST_KEY = re.compile(r"fallback|alias|allowlist|allowed[_-]?models?", re.IGNORECASE)
REGISTRY_FILES = (
    "llm-models.d/35-opencode.yml",
    "llm-models.d/40-hermes-cloud.yml",
    "llm-models.d/50-openrouter.yml",
)


def _strings(value: object):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _strings(nested)
    elif isinstance(value, str):
        yield value


def _name_list_offenders(value: object, location: str = ""):
    if isinstance(value, dict):
        has_alias_or_allowlist = any(
            re.search(r"alias|allowlist|allowed[_-]?models?", str(key), re.IGNORECASE)
            for key in value
        )
        for key, nested in value.items():
            field = str(key)
            field_location = f"{location}.{field}" if location else field
            is_named_list = bool(NAME_LIST_KEY.search(field)) or (
                field.casefold() in {"models", "model_names"} and has_alias_or_allowlist
            )
            if is_named_list:
                for name in _strings(nested):
                    if HOSTED_GLM_NAME.search(name):
                        yield f"{field_location}: {name}"
            else:
                yield from _name_list_offenders(nested, field_location)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _name_list_offenders(nested, f"{location}[{index}]")


def test_hosted_glm_routes_and_router_name_lists_are_absent() -> None:
    offenders = []
    for relative in REGISTRY_FILES:
        path = REPO_ROOT / relative
        if HOSTED_GLM_NAME.search(path.read_text(encoding="utf-8")):
            offenders.append(f"{relative}: contains a hosted GLM route name")

    for source_root in (REPO_ROOT / "roles/llm_router", REPO_ROOT / "inventory"):
        for path in sorted((*source_root.rglob("*.yml"), *source_root.rglob("*.yaml"))):
            relative = str(path.relative_to(REPO_ROOT))
            for document in yaml.load_all(path.read_text(encoding="utf-8"), Loader=_Permissive):
                offenders.extend(
                    f"{relative} [{name}]"
                    for name in _name_list_offenders(document)
                )

    hermes_registry = yaml.safe_load((REPO_ROOT / REGISTRY_FILES[1]).read_text(encoding="utf-8"))
    hermes_entries = next(iter(hermes_registry.values()))
    kimi = next(
        entry
        for entry in hermes_entries
        if entry.get("client_model_id", "").endswith("kimi-k2.6")
    )
    route_group = kimi["route_group"]
    openrouter_group = [
        entry for entry in hermes_entries if entry.get("route_group") == route_group
    ]
    assert openrouter_group
    assert all(
        entry.get("upstream_model_id") == kimi.get("upstream_model_id")
        and entry.get("litellm_model_name") == kimi.get("litellm_model_name")
        for entry in openrouter_group
    ), "The retained Hermes OpenRouter group must resolve only to its Kimi K2.6 model"
    assert not offenders, "Hosted GLM names remain in router routes or name lists:\n  " + "\n  ".join(
        offenders
    )
