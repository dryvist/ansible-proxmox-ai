"""Shared dated model admission for trials and router registry projections."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any


def policy_date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Model policy requires a dated upstream and as_of (YYYY-MM-DD)")
    return date.fromisoformat(value)


def denied_ids(policy: dict[str, Any]) -> set[str]:
    denied = set()
    for entry in policy["deny"]:
        model_id = entry.get("id") if isinstance(entry, dict) else entry
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("Model policy deny entries require a model ID")
        denied.add(model_id)
    return denied


def _validate_policy(policy: dict[str, Any], today: date) -> None:
    if not isinstance(policy, dict) or policy.get("version") != 1:
        raise ValueError("Model policy version 1 is required")
    if policy_date(policy.get("as_of")) > today:
        raise ValueError("Model policy as_of cannot be in the future")
    max_age = policy.get("max_age_days")
    if isinstance(max_age, bool) or not isinstance(max_age, int) or not 0 < max_age <= 365:
        raise ValueError("Model policy max_age_days must be between 1 and 365")
    if not isinstance(policy.get("deny"), list) or not isinstance(policy.get("models"), list):
        raise ValueError("Model policy requires deny and models lists")
    denied_ids(policy)


def model_policy_entry(
    model_ids: list[str], policy: dict[str, Any], today: date | None = None
) -> dict[str, Any]:
    """Resolve one policy record and enforce its upstream age and dossier."""
    today = today or datetime.now(timezone.utc).date()
    _validate_policy(policy, today)
    matches = []
    for model_id in model_ids:
        matches = [entry for entry in policy["models"]
                   if isinstance(entry, dict) and entry.get("id") == model_id]
        if matches:
            break
    if len(matches) != 1:
        raise ValueError("Model must resolve to exactly one shared policy record")
    entry = matches[0]
    if not isinstance(entry.get("upstream_id"), str) or not entry["upstream_id"]:
        raise ValueError("Model policy requires an upstream_id")
    if set(model_ids + [entry["upstream_id"]]).intersection(denied_ids(policy)):
        raise ValueError("Model or upstream is denied by the shared policy")
    created = policy_date(entry.get("upstream_created"))
    if not 0 <= (today - created).days <= policy["max_age_days"]:
        raise ValueError("Model upstream is old or future-dated under the shared policy")
    if not isinstance(entry.get("dossier"), (str, dict)) or not entry["dossier"]:
        raise ValueError("Model policy requires a dossier")
    return entry


def registry_policy_allowed(model: dict[str, Any], policy: dict[str, Any]) -> bool:
    _validate_policy(policy, datetime.now(timezone.utc).date())
    if model.get("lifecycle") == "old":
        return False
    try:
        model_policy_entry([model["client_model_id"], model["upstream_model_id"]], policy)
    except ValueError:
        return False
    return True


def base_models(info: dict[str, Any]) -> list[str]:
    bases = (info.get("cardData") or {}).get("base_model", [])
    if isinstance(bases, str):
        bases = [bases]
    if not isinstance(bases, list) or not all(
        isinstance(value, str) and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value
        ) for value in bases
    ):
        raise ValueError("Hub cardData base_model must contain valid repository IDs")
    return bases


def upstream_dates(
    info: dict[str, Any], ancestors: dict[str, dict[str, Any]], path: tuple[str, ...] = ()
) -> list[tuple[str, date]]:
    model_id = info.get("id")
    if not isinstance(model_id, str) or not model_id or model_id in path or len(path) >= 16:
        raise ValueError("Hub base_model chain is missing an ID, cyclic, or too deep")
    bases = base_models(info)
    if bases:
        dates = []
        for base in bases:
            if base not in ancestors or ancestors[base].get("id") != base:
                raise ValueError("Hub base_model chain has unresolved upstream metadata")
            dates.extend(upstream_dates(ancestors[base], ancestors, path + (model_id,)))
        return dates
    created = info.get("createdAt")
    if not isinstance(created, str) or not created:
        raise ValueError("Hub upstream model is undated: createdAt is required")
    return [(model_id, datetime.fromisoformat(created.replace("Z", "+00:00")).date())]


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {
            "llm_model_policy_allowed": registry_policy_allowed,
            "llm_model_trial_base_models": base_models,
        }
