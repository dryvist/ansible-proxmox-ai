#!/usr/bin/env python3
"""Build/refresh the homelab RAG index in Qdrant from the configured sources.

Reads {data_dir}/config.yaml (rendered by the llamaindex Ansible role), fetches
each source (the docs sites' llms-full.txt plus the local service registry),
embeds via the OpenAI-compatible fabric router, and (re)builds a single Qdrant
collection. This is the consumer that turns the otherwise-idle Qdrant into a
queryable RAG index.

Run by the llamaindex-index.timer systemd unit; safe to run by hand:
    /opt/llamaindex/venv/bin/python /opt/llamaindex/index_docs.py --dry-run

ponytail: full reindex each run (recreate the collection). Fine for a handful
of small llms-full.txt sources; switch to incremental (per-doc hash -> skip
unchanged) only if the source set or embed cost grows enough to notice.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

import yaml


def load_config(config_path: Path) -> dict:
    with config_path.open() as fh:
        return yaml.safe_load(fh)


def fetch_url(name: str, source: dict) -> str | None:
    """Return the source text, or None to skip it.

    A source may name a Cloudflare Access service-token env pair via
    cf_access_client_id_env / cf_access_client_secret_env. When those env vars
    are absent the source is skipped (non-fatal) so a converge without the
    token still indexes every reachable source.
    """
    headers = {"User-Agent": "homelab-rag-indexer"}
    cid_env = source.get("cf_access_client_id_env")
    csec_env = source.get("cf_access_client_secret_env")
    if cid_env or csec_env:
        cid = os.environ.get(cid_env or "")
        csec = os.environ.get(csec_env or "")
        if not (cid and csec):
            print(f"skip {name}: Cloudflare Access token env not set", file=sys.stderr)
            return None
        headers["CF-Access-Client-Id"] = cid
        headers["CF-Access-Client-Secret"] = csec
    req = urllib.request.Request(source["url"], headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted config URLs)
        return resp.read().decode("utf-8", "replace")


def read_path(name: str, source: dict) -> str | None:
    path = Path(source["path"])
    if not path.is_file():
        print(f"skip {name}: {path} not found", file=sys.stderr)
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def gather_documents(cfg: dict) -> list:
    from llama_index.core import Document

    docs = []
    for source in cfg.get("index", {}).get("sources", []):
        name = source.get("name", "source")
        text = fetch_url(name, source) if source.get("url") else read_path(name, source)
        if not text:
            continue
        docs.append(Document(text=text, metadata={"source": name}))
        print(f"loaded {name}: {len(text)} chars", file=sys.stderr)
    return docs


def build_index(cfg: dict, docs: list) -> None:
    import qdrant_client
    from llama_index.core import Settings, StorageContext, VectorStoreIndex
    from llama_index.embeddings.openai import OpenAIEmbedding
    from llama_index.vector_stores.qdrant import QdrantVectorStore

    qdrant = cfg["qdrant"]
    emb = cfg["embeddings"]
    collection = cfg["index"]["collection"]

    # OpenAI-compatible embeddings served by the fabric router. model_name is
    # the router alias, not a real OpenAI model. If this llama-index version
    # rejects an unknown model name, pin a known name here and rely on api_base
    # routing -- the router maps by deployment, not by the model string.
    Settings.embed_model = OpenAIEmbedding(
        model_name=emb["model"],
        api_base=emb["base_url"],
        api_key=emb["api_key"],
        # The embeddings tier is a single-slot llama.cpp server: keep request
        # batches small and retry through transient 429s instead of dying.
        embed_batch_size=8,
        max_retries=8,
    )
    # The serving tier embeds one input per ubatch (now sized to ctx via the
    # llama_cpp template). Chunks are still kept small because llama-index
    # counts tiktoken tokens while the nomic tokenizer runs 1.6-2.4x that on
    # dense technical text (384 tiktoken -> 614 nomic observed), and small
    # chunks retrieve better for terse infra docs anyway.
    Settings.chunk_size = 256
    Settings.chunk_overlap = 32

    client = qdrant_client.QdrantClient(
        host=qdrant["host"],
        port=int(qdrant["port"]),
        api_key=qdrant.get("api_key") or None,
        # qdrant-client silently flips to TLS when api_key is set; this Qdrant
        # speaks plain HTTP inside the VLAN, so pin the scheme explicitly.
        https=False,
    )
    # Full rebuild: drop the collection first so re-runs replace rather than
    # duplicate points.
    if client.collection_exists(collection):
        client.delete_collection(collection)
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection,
        # Named vector matching mcp-server-qdrant's FastEmbedProvider
        # convention (fast-<lowercased model>), so the gateway's read-only
        # docs-search sidecar can query this collection directly.
        dense_vector_name=cfg["index"]["dense_vector_name"],
    )
    storage = StorageContext.from_defaults(vector_store=vector_store)
    VectorStoreIndex.from_documents(docs, storage_context=storage)
    print(
        f"indexed {len(docs)} documents into qdrant collection '{collection}'",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the homelab RAG index in Qdrant.")
    parser.add_argument("--config", default="/opt/llamaindex/config.yaml", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch sources and report sizes; do not embed or write to Qdrant",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    docs = gather_documents(cfg)
    if not docs:
        print("no sources produced content; nothing to index", file=sys.stderr)
        return 1
    if args.dry_run:
        total = sum(len(doc.text) for doc in docs)
        print(f"dry-run: {len(docs)} documents ready, {total} total chars", file=sys.stderr)
        return 0
    build_index(cfg, docs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
