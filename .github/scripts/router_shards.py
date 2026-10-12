"""Weight-aware packing for the LLM Router contract matrix."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

MAX_ROUTER_SHARDS = 16
MAX_ROUTER_SHARD_NAME = 60


class RouterShard(TypedDict):
    name: str
    playbooks: str


class RouterShardBuilder(TypedDict):
    units: list[tuple[str, str, list[str], int]]
    playbooks: list[str]
    weight: int


def load_router_weights() -> dict[str, int]:
    # The scope job runs bare `python` with no packages installed, so read the mapping with the stdlib.
    path = Path(__file__).resolve().parents[1] / "router-contract-weights.yml"
    return {match[1]: int(match[2])
            for line in path.read_text(encoding="utf-8").splitlines()
            if (match := re.match(r"\s+(tests/\S+):\s*(\d+)\s*$", line))}


ROUTER_WEIGHTS = load_router_weights()


def router_weight(playbook: str) -> int:
    return ROUTER_WEIGHTS.get(playbook, 1)


def router_topic(playbook: str) -> str:
    stem = re.sub(r"^(?:test|verify)_", "", Path(playbook).stem)
    return stem.split("_", 1)[0]


def router_shard_target(matrix: list[list[str]]) -> int:
    playbooks = [playbook for entry in matrix for playbook in entry]
    shard_count = min(MAX_ROUTER_SHARDS, len(playbooks))
    if not shard_count:
        return 1
    total_weight = sum(router_weight(playbook) for playbook in playbooks)
    return max(1, (total_weight + shard_count - 1) // shard_count)


def router_shards(matrix: list[list[str]], shard_target: int | None = None) -> list[RouterShard]:
    playbooks = sorted(playbook for entry in matrix for playbook in entry)
    if not playbooks:
        return []

    target = max(1, shard_target if shard_target is not None else router_shard_target(matrix))
    total_weight = sum(router_weight(playbook) for playbook in playbooks)
    shard_count = min(MAX_ROUTER_SHARDS, len(playbooks), (total_weight + target - 1) // target)
    by_topic: dict[str, list[str]] = {}
    for playbook in playbooks:
        by_topic.setdefault(router_topic(playbook), []).append(playbook)

    units: list[tuple[str, str, list[str], int]] = []
    for topic, paths in sorted(by_topic.items()):
        chunks: list[tuple[list[str], int]] = []
        chunk: list[str] = []
        chunk_weight = 0
        for playbook in paths:
            weight = router_weight(playbook)
            if chunk and chunk_weight + weight > target:
                chunks.append((chunk, chunk_weight))
                chunk = []
                chunk_weight = 0
            chunk.append(playbook)
            chunk_weight += weight
        if chunk:
            chunks.append((chunk, chunk_weight))

        chunk_count = len(chunks)
        for index, (chunk_paths, weight) in enumerate(chunks):
            label = f"{topic} {index + 1}/{chunk_count}" if chunk_count > 1 else topic
            units.append((topic, label, chunk_paths, weight))
    units.sort(key=lambda unit: (-unit[3], unit[1]))

    shards: list[RouterShardBuilder] = [
        {"units": [], "playbooks": [], "weight": 0} for _ in range(shard_count)
    ]
    for unit in units:
        topic = unit[0]
        candidates = [shard for shard in shards
                      if all(existing[0] != topic for existing in shard["units"])]
        if not candidates:
            candidates = shards
        shard = min(candidates, key=lambda item: (item["weight"],
                                                   sorted(existing[1] for existing in item["units"])))
        shard["units"].append(unit)
        shard["playbooks"].extend(unit[2])
        shard["weight"] += unit[3]

    result: list[RouterShard] = []
    used_names: set[str] = set()
    for shard in shards:
        if not shard["units"]:
            continue
        topics = ", ".join(sorted(unit[1] for unit in shard["units"]))
        name = topics[:MAX_ROUTER_SHARD_NAME - 1].rstrip() + "…" if len(topics) > MAX_ROUTER_SHARD_NAME else topics
        if name in used_names:
            suffix = "… 2"
            name = name[:MAX_ROUTER_SHARD_NAME - len(suffix)] + suffix
            suffix_index = 3
            while name in used_names:
                suffix = f"… {suffix_index}"
                name = name[:MAX_ROUTER_SHARD_NAME - len(suffix)] + suffix
                suffix_index += 1
        used_names.add(name)
        result.append({"name": name, "playbooks": " ".join(sorted(shard["playbooks"]))})
    return sorted(result, key=lambda shard: shard["name"])
