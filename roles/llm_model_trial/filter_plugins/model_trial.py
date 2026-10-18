"""Pure Hub-fact selection for the one-run model trial role."""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from typing import Any

import yaml


_CONTEXT_RE = re.compile(
    r"(?i)\b(?:context(?:\s+length|\s+window)?|max(?:imum)?\s+context(?:\s+length)?)"
    r"\s*(?:is|of|up\s+to|:|=|-)?\s*([0-9][0-9,]*)\s*([km])?"
)
_GGUF_BITS_RE = re.compile(r"(?i)(?:UD-)?(?:IQ|Q)([0-9])")
_GGUF_LABEL_RE = re.compile(r"(?i)(?:UD-)?(?:IQ[0-9][A-Z0-9_]*|Q[0-9][A-Z0-9_]*)")
_GGUF_SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
_CONTEXT_KEYS = (
    "max_position_embeddings",
    "max_sequence_length",
    "model_max_length",
    "max_context_length",
    "context_length",
)


def _path(file: dict[str, Any]) -> str:
    return str(file.get("path", file.get("rfilename", "")))


def _sized_files(tree: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in tree:
        name = _path(item)
        size = item.get("size")
        if not name or not isinstance(size, int) or size < 0:
            raise ValueError("Hub file metadata must include each path and a non-negative size")
        if name in result:
            raise ValueError(f"Hub file tree contains duplicate path {name!r}")
        result[name] = {"path": name, "size": size}
        commit = item.get("last_commit")
        if isinstance(commit, dict) and commit.get("date"):
            result[name]["last_commit_date"] = commit["date"]
    if not result:
        raise ValueError("Hub returned an empty model file tree")
    return result


def _metadata_paths(files: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        name
        for name, file in files.items()
        if file["size"] <= 64 * 1024 * 1024
        and not name.lower().endswith((".gguf", ".safetensors", ".bin", ".pt", ".pth"))
        and not name.startswith(".gitattributes")
    )


def _context_values(config: dict[str, Any], info: dict[str, Any]) -> list[int]:
    values: list[int] = []
    for section in (config, config.get("text_config", {}), config.get("language_config", {})):
        if not isinstance(section, dict):
            continue
        for key in _CONTEXT_KEYS:
            value = section.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 1_000_000_000:
                values.append(value)
    gguf_context = (info.get("gguf") or {}).get("context_length")
    if isinstance(gguf_context, int) and 0 < gguf_context < 1_000_000_000:
        values.append(gguf_context)
    return values


def _card_context(card: str) -> int:
    front_matter = ""
    if card.startswith("---\n"):
        pieces = card.split("---", 2)
        if len(pieces) == 3:
            front_matter = pieces[1]
    if front_matter:
        metadata = yaml.safe_load(front_matter) or {}
        if isinstance(metadata, dict):
            for key in _CONTEXT_KEYS + ("context_window", "context_window_tokens"):
                value = metadata.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    return value
    for match in _CONTEXT_RE.finditer(card):
        value = int(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").lower()
        if suffix == "k":
            value *= 1000
        elif suffix == "m":
            value *= 1_000_000
        if value > 0:
            return value
    raise ValueError("The model card does not state a supported maximum context length")


def _quantization(config: dict[str, Any], hf_quant: dict[str, Any]) -> str:
    method = str((config.get("quantization_config") or {}).get("quant_method", "")).lower()
    quantization = hf_quant.get("quantization") or {}
    algorithms = {
        str(item.get("quant_algo", "")).upper()
        for item in (quantization.get("quantized_layers") or {}).values()
        if isinstance(item, dict)
    }
    if method == "modelopt" and "NVFP4" in algorithms:
        return "modelopt_nvfp4"
    if method:
        return method
    producer = str((hf_quant.get("producer") or {}).get("name", "")).lower()
    return producer


def _safetensors_weights(files: dict[str, dict[str, Any]], index: dict[str, Any]) -> list[str]:
    weight_map = index.get("weight_map") or {}
    if weight_map:
        names = sorted(set(weight_map.values()))
    else:
        names = sorted(name for name in files if name.endswith(".safetensors"))
    if not names or any(
        name not in files or not name.endswith(".safetensors") for name in names
    ):
        raise ValueError("Hub metadata does not resolve a complete safetensors weight set")
    return names


def _gguf_label(name: str) -> str:
    parent = name.split("/", 1)[0] if "/" in name else ""
    if parent and parent.lower() not in {"mtp", "mmproj"}:
        return parent
    match = _GGUF_LABEL_RE.search(name.rsplit("/", 1)[-1])
    return match.group(0) if match else ""


def _complete_gguf_shards(names: list[str]) -> bool:
    shards = [_GGUF_SHARD_RE.search(name) for name in names]
    if not any(shards):
        return True
    if any(shard is None for shard in shards):
        return False
    totals = {int(shard.group(2)) for shard in shards if shard}
    indexes = {int(shard.group(1)) for shard in shards if shard}
    if len(totals) != 1:
        return False
    total = next(iter(totals))
    return len(shards) == total and indexes == set(range(1, total + 1))


def _gguf_weights(files: dict[str, dict[str, Any]], minimum_bits: int) -> tuple[str, list[str], bool]:
    groups: dict[str, list[str]] = defaultdict(list)
    mtp_names: list[str] = []
    for name in files:
        if not name.lower().endswith(".gguf"):
            continue
        lower = name.lower()
        if "/mtp/" in f"/{lower}/" or lower.rsplit("/", 1)[-1].startswith("mtp-"):
            mtp_names.append(name)
            continue
        if "/mmproj/" in f"/{lower}/" or lower.rsplit("/", 1)[-1].startswith("mmproj-"):
            continue
        label = _gguf_label(name)
        bit_match = _GGUF_BITS_RE.search(label)
        if label and bit_match and int(bit_match.group(1)) >= minimum_bits:
            groups[label].append(name)
    groups = {label: names for label, names in groups.items() if _complete_gguf_shards(names)}
    if not groups:
        raise ValueError("Hub tree has no complete GGUF quantization at or above the configured bit floor")
    label, names = min(
        groups.items(),
        key=lambda item: (sum(files[name]["size"] for name in item[1]), item[0]),
    )
    mtp = any(
        match.group(0).casefold() == label.casefold()
        for name in mtp_names
        if (match := _GGUF_LABEL_RE.search(name))
    )
    return label, sorted(names), mtp


def _matches(rule: dict[str, Any], facts: dict[str, Any]) -> bool:
    return all(
        fnmatch.fnmatchcase(str(facts[key]), str(rule[key]))
        for key in ("format", "quantization", "architecture")
    ) and rule.get("mtp_head") is facts["mtp_head"]


def select_model_trial(
    info: dict[str, Any],
    tree: list[dict[str, Any]],
    config: dict[str, Any],
    generation_config: dict[str, Any],
    hf_quant: dict[str, Any],
    weight_index: dict[str, Any],
    card: str,
    rules: dict[str, Any],
    requested_engine: str = "auto",
    requested_context: int | str | None = None,
    requested_slots: int | str | None = None,
) -> dict[str, Any]:
    """Validate recorded Hub facts and select one engine/weight set without network access."""
    if info.get("private") is not False or info.get("gated") is not False:
        raise ValueError("Only public, ungated model repositories are accepted")
    author = str(info.get("author") or "").casefold()
    trusted = {str(value).casefold() for value in rules["llm_model_trial_trusted_authors"]}
    if author not in trusted and not (
        int(info.get("likes") or 0) >= rules["llm_model_trial_minimum_likes"]
        and int(info.get("downloads") or 0) >= rules["llm_model_trial_minimum_downloads"]
    ):
        raise ValueError("Model author is not trusted by the allowlist or popularity threshold")
    sha = str(info.get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Hub did not resolve the requested revision to an immutable 40-character SHA")

    files = _sized_files(tree)
    present = set(files)
    if "README.md" not in present:
        raise ValueError("Hub tree must contain the model card")
    if "generation_config.json" in present and not isinstance(generation_config, dict):
        raise ValueError("generation_config.json did not parse as an object")

    safetensors = any(name.endswith(".safetensors") for name in files)
    gguf = any(name.lower().endswith(".gguf") for name in files)
    if safetensors and "config.json" not in present:
        raise ValueError("Hub tree must contain config.json for safetensors models")
    candidates: list[tuple[str, str, str, list[str], bool]] = []
    if safetensors:
        quant = _quantization(config, hf_quant)
        weights = _safetensors_weights(files, weight_index)
        index_keys = (weight_index.get("weight_map") or {}).keys()
        mtp = any("mtp." in str(key).lower() for key in index_keys)
        architecture = str(config.get("model_type") or ((config.get("architectures") or [""])[0])).casefold()
        candidates.append(("safetensors", quant, architecture, weights, mtp))
    if gguf:
        quant, weights, mtp = _gguf_weights(files, int(rules["llm_model_trial_gguf_minimum_bits"]))
        architecture = str((info.get("gguf") or {}).get("architecture") or "").casefold()
        candidates.append(("gguf", quant, architecture, weights, mtp))
    if not candidates:
        raise ValueError("Hub tree does not contain a supported model weight format")

    card_max = _card_context(card)
    config_maxima = _context_values(config, info)
    if not config_maxima:
        raise ValueError("Hub config does not state a maximum context length")
    config_max = min(config_maxima)
    if max(config_maxima) > min(config_maxima) * 1.01:
        raise ValueError("Hub config contains contradictory maximum context values")
    if max(card_max, config_max) > min(card_max, config_max) * 1.01:
        raise ValueError("The model card and config disagree on maximum context")
    context_max = min(card_max, config_max)

    selected: tuple[dict[str, Any], str, str, list[str], bool] | None = None
    requested_engine = requested_engine or "auto"
    if requested_engine not in ("auto", "vllm", "llama_cpp"):
        raise ValueError("engine must be auto, vllm, or llama_cpp")
    for file_format, quantization, architecture, weights, mtp in candidates:
        if not architecture:
            continue
        facts = {
            "format": file_format,
            "quantization": quantization,
            "architecture": architecture,
            "mtp_head": mtp,
        }
        matches = [rule for rule in rules["llm_model_trial_engine_rules"] if _matches(rule, facts)]
        if len(matches) != 1:
            continue
        rule = matches[0]
        if requested_engine not in ("auto", rule["engine"]):
            continue
        selected = (rule, quantization, architecture, weights, mtp)
        break
    if selected is None:
        raise ValueError("No engine rule matches the Hub format, quantization, architecture, and MTP facts")

    rule, quantization, architecture, weight_files, mtp_head = selected
    if len(weight_files) != len(set(weight_files)):
        raise ValueError("Selected model weight paths are not unique")
    if requested_context is None:
        context = min(int(rule["default_context"]), context_max)
    else:
        context = int(requested_context)
    slots = 1 if requested_slots is None else int(requested_slots)
    if context < 1 or context > context_max or context > int(rule["maximum_context"]):
        raise ValueError(f"Requested context {context} exceeds the validated maximum {min(context_max, int(rule['maximum_context']))}")
    if slots < 1 or slots > int(rule["maximum_slots"]):
        raise ValueError(f"Requested slots {slots} exceed the validated maximum {rule['maximum_slots']}")

    metadata = _metadata_paths(files)
    selected_names = sorted(set(weight_files + metadata))
    selected_files = [files[name] for name in selected_names]
    commit_dates = [
        str(file["last_commit_date"])
        for file in files.values()
        if file.get("last_commit_date")
    ]
    return {
        "rule_version": int(rules["llm_model_trial_engine_rules_version"]),
        "resolved_sha": sha,
        "author": info.get("author"),
        "likes": int(info.get("likes") or 0),
        "downloads": int(info.get("downloads") or 0),
        "created_at": info.get("createdAt"),
        "last_modified": info.get("lastModified"),
        "release_date": info.get("lastModified") or (max(commit_dates) if commit_dates else None),
        "format": "safetensors" if selected[0]["format"] == "safetensors" else "gguf",
        "quantization": quantization,
        "architecture": architecture,
        "mtp_head": mtp_head,
        "engine": rule["engine"],
        "context": context,
        "context_maximum": context_max,
        "slots": slots,
        "flags": rule["flags"],
        "weight_files": [files[name] for name in weight_files],
        "files": selected_files,
        "generation_config": generation_config,
    }


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {
            "llm_model_trial_select": select_model_trial,
            "llm_model_trial_metadata_paths": lambda tree: _metadata_paths(_sized_files(tree)),
        }
