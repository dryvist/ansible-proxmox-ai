"""Conservative, network-free trial proposals from Hub model-info responses."""

import fnmatch
import hashlib
import math
import re
from collections import defaultdict


_GGUF = re.compile(r"(?i)((?:IQ|Q)[3-8][A-Z0-9_]*)")
_SHARD = re.compile(r"-(\d{5})-of-(\d{5})\.(?:gguf|safetensors)$", re.I)


def select_model_watch(models, accelerators, watched_orgs):
    """Return pinned requests; unknown size/quantization never implies a fit."""
    requests = {}
    trusted = {org.casefold() for org in watched_orgs}
    for accelerator in accelerators:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", accelerator["id"]):
            raise ValueError("Accelerator id must be a safe filename component")
        budget = (float(accelerator["vram_gib"]) - float(accelerator["reserve_gib"])) * 1024**3
        if not math.isfinite(budget) or budget <= 0 or not accelerator["quantizations"]:
            raise ValueError("Accelerators require positive usable VRAM and allowed quantizations")
        for info in models:
            repo = info.get("id", "")
            sha = info.get("sha", "")
            if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo)
                    or repo.split("/")[0].casefold() not in trusted
                    or not re.fullmatch(r"[a-f0-9]{40}", sha)
                    or info.get("private") is not False or info.get("gated") is not False):
                continue
            if info.get("library_name") not in accelerator["libraries"]:
                continue
            groups = defaultdict(list)
            for file in info.get("siblings", []):
                name = file.get("rfilename", "")
                if any(part.lower() in {"mtp", "mmproj"} for part in name.split("/")):
                    continue
                match = _GGUF.search(name.rsplit("/", 1)[-1])
                if name.lower().endswith(".gguf") and match:
                    shard = _SHARD.search(name)
                    family = (name[:shard.start()], int(shard[2])) if shard else (name[:-5], 0)
                    groups[("gguf", match[1].upper(), family)].append(file)
                elif name.endswith(".safetensors"):
                    config = info.get("config") or {}
                    quant = config.get("quantization_config") or config.get("quantization") or {}
                    method = quant.get("quant_method", "")
                    if not method and info.get("library_name") == "mlx":
                        method = f"mlx-{quant.get('bits', '')}bit"
                    if method:
                        groups[("safetensors", method, "")].append(file)
            choices = []
            for (format_name, quant, _), files in groups.items():
                if format_name not in accelerator["formats"] or not any(
                    fnmatch.fnmatchcase(quant.casefold(), pattern.casefold())
                    for pattern in accelerator["quantizations"]
                ):
                    continue
                sizes = [file.get("size", (file.get("lfs") or {}).get("size")) for file in files]
                if not all(type(size) is int and size > 0 for size in sizes):
                    continue
                shards = [_SHARD.search(file["rfilename"]) for file in files]
                if any(shards):
                    if not all(shards):
                        continue
                    totals = {int(shard[2]) for shard in shards}
                    if len(totals) != 1 or len(files) != next(iter(totals)) or {
                        int(shard[1]) for shard in shards
                    } != set(range(1, len(files) + 1)):
                        continue
                size = sum(sizes)
                if size <= budget:
                    choices.append((size, quant, format_name, sorted(file["rfilename"] for file in files)))
            if not choices:
                continue
            size, quant, format_name, weight_files = min(choices)
            identity = f"{repo}@{sha}:{accelerator['id']}:{weight_files}"
            key = hashlib.sha256(identity.encode()).hexdigest()
            requests[key] = {
                "hf_repo": repo, "revision": sha, "accelerator": accelerator["id"],
                "quantization": quant, "format": format_name, "weight_bytes": size,
                "weight_files": weight_files,
            }
    return [{"key": key, **value} for key, value in sorted(requests.items())]


class FilterModule:
    def filters(self):
        return {"llm_model_watch_select": select_model_watch}
