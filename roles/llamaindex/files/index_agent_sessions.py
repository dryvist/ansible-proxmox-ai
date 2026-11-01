#!/usr/bin/env python3
"""Incrementally embed agent session logs from Splunk into Qdrant."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def event_epoch(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        timestamp = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()


def field_ref(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.]+", value):
        raise ValueError("invalid Splunk field name")
    return f"'{value}'"


def build_search(harness: dict, watermark: float) -> str:
    index = harness["index"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", index):
        raise ValueError("invalid Splunk index name")
    return "\n".join(
        (
            f"search index={index}",
            "| eval _agent_role=" + field_ref(harness["role_field"])
            + ", _agent_content=" + field_ref(harness["content_field"])
            + ", _agent_session=" + field_ref(harness["session_field"])
            + ", _agent_cwd=" + field_ref(harness["cwd_field"]),
            '| where _agent_role="assistant" OR _agent_role="user"',
            "| where isnotnull(_agent_session)",
            "| sort 0 _agent_session _time",
            "| streamstats current=f last(eval(if(_agent_role=\"assistant\", "
            "_agent_content, null()))) as _agent_assistant_prev by _agent_session",
            '| where _agent_role="user" AND _time >= ' + f"{watermark:.6f}",
            "| table _time _agent_session _agent_cwd _agent_assistant_prev _agent_content",
        )
    )


EXPORT_ATTEMPTS = 6
EXPORT_RETRY_DELAYS = (10, 30, 60, 120, 240)  # seconds slept after attempts 1 through 5


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (401, 429) or 500 <= exc.code <= 599
    return isinstance(exc, (urllib.error.URLError, socket.timeout))


def open_export(request: urllib.request.Request):
    # Only the connection phase is retried. A failed attempt has yielded no
    # rows yet, so a retry cannot duplicate rows or advance the watermark.
    attempt = 0
    while True:
        try:
            return urllib.request.urlopen(request, timeout=120)  # trusted config URL
        except (urllib.error.URLError, socket.timeout) as exc:
            if not is_transient(exc):
                raise
            # Status and class only: the final error log reads the HTTP body.
            status = f" status={exc.code}" if isinstance(exc, urllib.error.HTTPError) else ""
            label = (
                f"agent session export attempt {attempt + 1} of {EXPORT_ATTEMPTS} failed: "
                f"{type(exc).__name__}{status}"
            )
            if attempt == EXPORT_ATTEMPTS - 1:
                print(f"{label}, no attempts left", file=sys.stderr)
                raise
            delay = EXPORT_RETRY_DELAYS[attempt]
            print(f"{label}, retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)
            attempt += 1


def export_rows(url: str, token: str, search: str):
    endpoint = url.rstrip("/") + "/services/search/jobs/export"
    body = urllib.parse.urlencode({"search": search, "output_mode": "json"}).encode()
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with open_export(request) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            item = json.loads(line)
            result = item.get("result") if isinstance(item, dict) else None
            if isinstance(result, dict):
                yield result


def read_watermarks(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        return {name: float(value) for name, value in json.load(stream).items()}


def write_watermarks(path: Path, watermarks: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(watermarks, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def point_id(session_id: str, event_time: str) -> str:
    digest = hashlib.sha256(f"{session_id}\0{event_time}".encode()).hexdigest()[:32]
    return str(uuid.UUID(hex=digest))


def make_clients(base_config: dict, collection: str):
    import qdrant_client
    from llama_index.embeddings.openai import OpenAIEmbedding
    from qdrant_client import models

    embedding = base_config["embeddings"]
    embedder = OpenAIEmbedding(
        model_name=embedding["model"],
        api_base=embedding["base_url"],
        api_key=embedding["api_key"],
        embed_batch_size=8,
        max_retries=8,
    )
    qdrant = base_config["qdrant"]
    client = qdrant_client.QdrantClient(
        host=qdrant["host"],
        port=int(qdrant["port"]),
        api_key=qdrant.get("api_key") or None,
        https=False,
        timeout=120,
    )
    if not client.collection_exists(collection):
        dimension = len(embedder.get_text_embedding("agent session logs collection dimension"))
        client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )
    return client, embedder


def index_harness(
    harness: dict,
    splunk: dict,
    collection: str,
    watermark: float,
    client,
    embedder,
) -> float:
    from qdrant_client import models

    latest = watermark
    points = []
    for row in export_rows(splunk["url"], splunk["token"], build_search(harness, watermark)):
        event_time = str(row["_time"])
        latest = max(latest, event_epoch(event_time))
        session_id = str(row.get("_agent_session") or "")
        user_text = str(row.get("_agent_content") or "")
        if not session_id or not user_text:
            continue
        assistant_prev = str(row.get("_agent_assistant_prev") or "")[:600]
        vector = embedder.get_text_embedding(assistant_prev + "\n---\n" + user_text)
        points.append(
            models.PointStruct(
                id=point_id(session_id, event_time),
                vector=vector,
                payload={
                    "harness": harness["name"],
                    "cwd": row.get("_agent_cwd"),
                    "session_id": session_id,
                    "time": event_time,
                    "user_text": user_text,
                },
            )
        )
    for start in range(0, len(points), 64):
        client.upsert(collection_name=collection, points=points[start : start + 64], wait=True)
    return latest


def describe_error(exc: Exception) -> str:
    kind = type(exc).__name__
    if isinstance(exc, urllib.error.HTTPError):
        try:
            # 800 bytes covers 200 characters of UTF-8.
            body = exc.read(800).decode("utf-8", "replace")[:200]
        except (OSError, http.client.HTTPException):
            body = ""
        return f"{kind} status={exc.code} body={body!r}"
    if isinstance(exc, urllib.error.URLError):
        return f"{kind} reason={exc.reason}"
    return kind


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--agent-config", required=True, type=Path)
    args = parser.parse_args()

    base_config = load_config(args.config)
    agent_config = load_config(args.agent_config)
    watermark_path = Path(agent_config["index"]["watermark_file"])
    watermarks = read_watermarks(watermark_path)
    collection = agent_config["index"]["collection"]
    client, embedder = make_clients(base_config, collection)

    for harness in agent_config["index"]["harnesses"]:
        name = harness["name"]
        try:
            latest = index_harness(
                harness,
                agent_config["splunk"],
                collection,
                watermarks.get(name, 0),
                client,
                embedder,
            )
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(f"agent session export failed for {name}: {describe_error(exc)}", file=sys.stderr)
            return 1
        watermarks[name] = latest
        write_watermarks(watermark_path, watermarks)
        print(f"indexed agent session logs for {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
