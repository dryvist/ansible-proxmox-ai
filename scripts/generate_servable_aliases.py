#!/usr/bin/env python3
"""Generate the published alias contract from the model registry.

The registry (repo-root llm-models.yml) is the single place any model name,
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
REGISTRY_FILE = REPO_ROOT / "llm-models.yml"


def load_registry(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    entries = (data or {}).get("llm_router_model_registry")
    if not entries:
        raise SystemExit(
            f"{path} defined no llm_router_model_registry entries. "
            "Every published name derives from that key, so an empty or "
            "renamed one would publish an empty contract rather than fail."
        )
    return entries


def build_contract(entries: list[dict]) -> dict:
    enabled = [e for e in entries if e.get("enabled")]

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

    models = [
        {
            "client_model_id": e["client_model_id"],
            "upstream_model_id": e["upstream_model_id"],
            "provider": e["provider"],
            "tier": e["tier"],
            "servable": bool(e.get("servable", False)),
            "context_window": e.get("context_window"),
            "stable_aliases": sorted(e.get("stable_aliases", [])),
        }
        for e in enabled
    ]
    models.sort(key=lambda m: (m["tier"], m["client_model_id"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "llm-models.yml",
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
    parser.add_argument("--registry", type=pathlib.Path, default=REGISTRY_FILE)
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
