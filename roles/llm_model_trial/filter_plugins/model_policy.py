"""Shared dated model admission for trials and router registry projections."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _validate_dossier(value: Any, root: str) -> None:
    prefix = Path("ansible/roles/llm_model_catalog/files/dossiers")
    if not isinstance(value, str) or not Path(value).is_relative_to(prefix):
        raise ValueError("Model policy requires a repo-relative dossier path")
    if not isinstance(root, str) or not root:
        raise ValueError("Model policy requires a dossier collection root")
    base = Path(root).resolve()
    path = (base / Path(value).relative_to("ansible")).resolve()
    if not path.is_relative_to(base / "roles/llm_model_catalog/files/dossiers"):
        raise ValueError("Model dossier path escapes the dossier directory")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"Model dossier file cannot be read: {value}") from error
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("Model dossier requires YAML front matter")
    try:
        metadata = yaml.safe_load("\n".join(lines[1:lines.index("---", 1)]))
    except yaml.YAMLError as error:
        raise ValueError("Model dossier front matter is invalid YAML") from error
    if not isinstance(metadata, dict) or metadata.get("type") != "Model Dossier":
        raise ValueError("Model dossier type must be Model Dossier")
    timestamp = metadata.get("timestamp")
    try:
        if not isinstance(timestamp, (date, datetime)):
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Model dossier requires a parseable timestamp") from error
    if metadata.get("status") != "complete":
        raise ValueError("Model dossier status must be complete")


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
    upstream_id: str, policy: dict[str, Any], today: date | None = None
) -> dict[str, Any]:
    """Resolve one policy record and enforce its upstream age and dossier."""
    today = today or datetime.now(timezone.utc).date()
    _validate_policy(policy, today)
    matches = [entry for entry in policy["models"]
               if isinstance(entry, dict) and entry.get("upstream_id") == upstream_id]
    if not upstream_id or not matches:
        raise ValueError("Model upstream_id has no shared policy record")
    entry = matches[0]
    if any((match.get("upstream_created"), match.get("dossier")) !=
           (entry.get("upstream_created"), entry.get("dossier")) for match in matches):
        raise ValueError("Model upstream_id has conflicting shared policy records")
    if upstream_id in denied_ids(policy):
        raise ValueError("Model or upstream is denied by the shared policy")
    created = policy_date(entry.get("upstream_created"))
    if not 0 <= (today - created).days <= policy["max_age_days"]:
        raise ValueError("Model upstream is old or future-dated under the shared policy")
    _validate_dossier(entry.get("dossier"), policy.get("_dossier_root", ""))
    return entry


def registry_policy_reason(model: dict[str, Any], policy: dict[str, Any]) -> str:
    """Return the admission rejection reason, or an empty string for eligible models."""
    _validate_policy(policy, datetime.now(timezone.utc).date())
    if model.get("lifecycle") == "old":
        return "Model lifecycle is old"
    if model.get("client_model_id") in denied_ids(policy):
        return "Model is denied by the shared policy"
    try:
        model_policy_entry(model.get("upstream_model_id", ""), policy)
    except ValueError as error:
        return str(error)
    return ""


def registry_policy_allowed(model: dict[str, Any], policy: dict[str, Any]) -> bool:
    return not registry_policy_reason(model, policy)


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
            "llm_model_policy_reason": registry_policy_reason,
            "llm_model_trial_base_models": base_models,
        }
