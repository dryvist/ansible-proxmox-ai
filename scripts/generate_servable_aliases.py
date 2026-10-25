#!/usr/bin/env python3
"""Generate the published alias contract from the model registry.

The registry (repo-root llm-models.d/) is the single place any model name,
alias, tier or enabled state is written. Consumers outside this repo — the
Hermes bundle's model manifest check, the workstation nix-ai module, the
delegation skills — need that inventory without cloning an Ansible role and
without re-spelling a model name locally. This emits it as one JSON document.

WHY A GENERATOR AND NOT A COMMITTED FILE. A committed copy is a second
spelling: it goes stale the moment someone edits the registry and forgets it,
which is the exact defect class the registry exists to remove. Git stays
authoritative for the registry; CI derives this artifact and attaches it to a
versioned release, so consumers pin an immutable revision with a checksum
rather than tracking a mutable branch. A converge never writes it back.

The registry is deliberately pure data — no Jinja, no host/port literals, no
secrets — precisely so this can parse it standalone with any YAML reader,
without an Ansible run and without importing the role's templating.

Output is byte-deterministic (sorted keys, fixed separators, trailing
newline): a checksum is only meaningful if the same input always produces the
same bytes.

Usage:
    python3 scripts/generate_servable_aliases.py            # to stdout
    python3 scripts/generate_servable_aliases.py -o out.json
    python3 scripts/generate_servable_aliases.py --check out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import yaml

# Bumped only on a breaking change to the document shape below. Consumers
# should refuse a version they do not understand rather than guess.
SCHEMA_VERSION = 1

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY_DIR = REPO_ROOT / "llm-models.d"


def load_registry(path: pathlib.Path) -> list[dict]:
    """Concatenate the per-tier registry slices, in filename order."""
    entries: list[dict] = []
    for slice_file in sorted(path.glob("*.yml")):
        with slice_file.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        for value in (data or {}).values():
            entries.extend(value)
    if not entries:
        raise SystemExit(
            f"{path} defined no registry entries. "
            "Every published name derives from it, so an empty or "
            "renamed one would publish an empty contract rather than fail."
        )
    return entries


def build_contract(entries: list[dict]) -> dict:
    enabled = [e for e in entries if e.get("enabled")]
    artifacts_by_id = {
        entry["artifact_id"]: entry
        for entry in entries
        if entry.get("artifact_id") and entry.get("hf_repo")
    }

    aliases: dict[str, str] = {}
    for entry in enabled:
        for alias in entry.get("stable_aliases", []):
            if alias in aliases:
                raise SystemExit(
                    f"alias {alias!r} is claimed by both "
                    f"{aliases[alias]!r} and {entry['client_model_id']!r}. "
                    "An alias resolving to two models is unroutable; fix the "
                    "registry rather than publishing an arbitrary winner."
                )
            aliases[alias] = entry["client_model_id"]

    # `model_group_alias` is the union of stable aliases projected from entries
    # the router admits as servable, plus its explicit long-context exception.
    # Keep that public alias vocabulary separate from direct routes below.
    long_context_alias_ids = {
        entry["client_model_id"]
        for entry in enabled
        if entry.get("tier") == "openrouter"
        and entry.get("context_window", 0) >= 1_000_000
        and entry.get("zero_data_retention") is True
    }
    served_aliases = sorted(
        {
            alias
            for entry in entries
            if (
                (entry.get("enabled") and entry.get("servable"))
                or entry.get("client_model_id") in long_context_alias_ids
            )
            for alias in entry.get("stable_aliases", [])
        }
    )

    # route_group is the model_list name when present; model_list templates use
    # client_model_id as the name for entries without a route_group. These names
    # stay registry-derived so inventory or Ansible rendering is not required.
    route_groups = sorted(
        {
            entry.get("route_group") or entry.get("client_model_id")
            for entry in enabled
            if entry.get("route_group") or entry.get("client_model_id")
        }
    )
    direct_routes = sorted(
        {
            route
            for entry in enabled
            for route in entry.get("direct_route_aliases", [])
        }
    )

    # These are LiteLLM's DB-seeded role targets. Review role names are stable
    # aliases from the registry and are already included in `served_aliases`.
    roles_path = REPO_ROOT / "roles/llm_router/defaults/main/55-roles.yml"
    roles_doc = yaml.safe_load(roles_path.read_text(encoding="utf-8")) or {}
    roles = sorted(
        {
            role["role"]
            for role in roles_doc.get("_llm_router_role_deployments_source", [])
            if isinstance(role, dict) and role.get("role")
        }
    )
    router_name_categories = {
        "aliases": served_aliases,
        "route_groups": route_groups,
        "direct_routes": direct_routes,
        "roles": roles,
    }
    router_names = {
        **router_name_categories,
        "all": sorted(
            set().union(*(set(names) for names in router_name_categories.values()))
        ),
    }

    models = []
    for entry in enabled:
        upstream_model_id = entry.get("upstream_model_id")
        if upstream_model_id is None:
            upstream_model_id = artifacts_by_id.get(entry.get("artifact_id"), {}).get("hf_repo")
        if upstream_model_id is None:
            raise SystemExit(
                f"enabled model {entry.get('client_model_id')!r} must set upstream_model_id "
                "or reference an artifact with hf_repo."
            )
        models.append(
            {
                "client_model_id": entry["client_model_id"],
                "upstream_model_id": upstream_model_id,
                "provider": entry["provider"],
                "tier": entry["tier"],
                "servable": bool(entry.get("servable", False)),
                "context_window": entry.get("context_window"),
                "stable_aliases": sorted(entry.get("stable_aliases", [])),
            }
        )
    models.sort(key=lambda m: (m["tier"], m["client_model_id"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "llm-models.d/",
        "aliases": dict(sorted(aliases.items())),
        "models": models,
        # The paid-egress allowlist, published explicitly rather than left for
        # consumers to re-derive by filtering `models` — a filter written in
        # three places is three chances to get the predicate wrong.
        "egress_allowlist": sorted(
            m["client_model_id"] for m in models if m["tier"] == "openrouter"
        ),
        # Alias targets a caller may rely on reaching. `servable` is distinct
        # from `enabled`: the serving host runs single-model, so a non-resident
        # model 404s rather than degrading. Publishing both lets a consumer
        # tell "offered" from "answerable".
        "servable_models": sorted(
            m["client_model_id"] for m in models if m["servable"]
        ),
        "router_names": router_names,
    }


def render(contract: dict) -> str:
    return json.dumps(contract, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=pathlib.Path)
    parser.add_argument(
        "--check",
        type=pathlib.Path,
        help="compare an existing file against freshly generated output and "
        "exit non-zero if they differ",
    )
    parser.add_argument("--registry", type=pathlib.Path, default=REGISTRY_DIR)
    parser.add_argument(
        "--print-sha256",
        action="store_true",
        help="write the digest to stderr (the artifact's release checksum)",
    )
    args = parser.parse_args()

    text = render(build_contract(load_registry(args.registry)))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    if args.check:
        existing = args.check.read_text(encoding="utf-8") if args.check.exists() else ""
        if existing != text:
            print(
                f"{args.check} does not match the registry. It is generated, "
                "not authored — regenerate it rather than editing it.",
                file=sys.stderr,
            )
            return 1
        return 0

    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)

    if args.print_sha256:
        print(digest, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
