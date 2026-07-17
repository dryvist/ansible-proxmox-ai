"""Docs-search MCP server: embed the query via the LLM router, search Qdrant.

Read-only by construction (search is the only tool). Embedding runs on the
serving tier through the router's OpenAI-compatible /embeddings endpoint —
the SAME model deployment the indexer used to build the collection — so no
embedding model is loaded in this container.

Configuration (env): ROUTER_BASE_URL, ROUTER_API_KEY, EMBED_MODEL,
QDRANT_URL, QDRANT_API_KEY, COLLECTION_NAME, DENSE_VECTOR_NAME,
FASTMCP_SERVER_HOST, FASTMCP_SERVER_PORT.
"""

import json
import os

import httpx
from fastmcp import FastMCP

mcp = FastMCP("docs-search")


@mcp.tool
async def search_docs(query: str, limit: int = 5) -> list[dict]:
    """Search the homelab documentation (infrastructure, services, runbooks,
    conventions). Takes a natural-language question and returns the most
    relevant documentation chunks with their relevance scores."""
    async with httpx.AsyncClient(timeout=60) as http:
        emb = await http.post(
            f"{os.environ['ROUTER_BASE_URL']}/embeddings",
            headers={"Authorization": f"Bearer {os.environ['ROUTER_API_KEY']}"},
            json={"model": os.environ["EMBED_MODEL"], "input": query},
        )
        emb.raise_for_status()
        vector = emb.json()["data"][0]["embedding"]

        search = await http.post(
            f"{os.environ['QDRANT_URL']}/collections/{os.environ['COLLECTION_NAME']}"
            "/points/search",
            headers={"api-key": os.environ["QDRANT_API_KEY"]},
            json={
                "vector": {"name": os.environ["DENSE_VECTOR_NAME"], "vector": vector},
                "limit": limit,
                "with_payload": True,
            },
        )
        search.raise_for_status()

    results = []
    for hit in search.json()["result"]:
        # llama-index stores the chunk text inside the _node_content JSON blob.
        node = json.loads(hit["payload"].get("_node_content", "{}"))
        results.append({"score": hit["score"], "text": node.get("text", "")})
    return results


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.environ.get("FASTMCP_SERVER_HOST", "0.0.0.0"),
        port=int(os.environ.get("FASTMCP_SERVER_PORT", "8001")),
        # Serve exactly at /mcp/ — the gateway route dials this path, and
        # FastMCP 3's default (/mcp) would 307 it out of the route.
        path="/mcp/",
    )
