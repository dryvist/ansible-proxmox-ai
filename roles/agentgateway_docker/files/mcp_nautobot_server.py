"""Nautobot IPAM/DCIM MCP server: a thin GraphQL passthrough over Nautobot's
own API. One tool, and Nautobot's GraphQL endpoint answers queries only (no
mutations), so schema introspection and data lookups share the same call.

Read-only by construction at TWO layers: this container never issues a write,
and the backing NAUTOBOT_READ_TOKEN is bound to a view-only ObjectPermission
(see ansible-proxmox-apps roles/nautobot/files/mcp_token_bootstrap.py) — even
a crafted mutation would be rejected by Nautobot's own permission system, not
by this shim.

Configuration (env): NAUTOBOT_URL, NAUTOBOT_READ_TOKEN, FASTMCP_SERVER_HOST,
FASTMCP_SERVER_PORT.
"""

import os

import httpx
from fastmcp import FastMCP

mcp = FastMCP("nautobot")


@mcp.tool
async def nautobot_graphql(query: str, variables: dict | None = None) -> dict:
    """Run a read-only GraphQL query against Nautobot (the homelab's IPAM/DCIM
    source of truth: devices, interfaces, IP addresses, prefixes, VLANs,
    locations, virtual machines). Unsure of the schema? Query it first:
    `{ __schema { queryType { fields { name } } } }`. Returns the raw
    {"data": ..., "errors": ...} GraphQL response."""
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            f"{os.environ['NAUTOBOT_URL']}/graphql/",
            headers={"Authorization": f"Token {os.environ['NAUTOBOT_READ_TOKEN']}"},
            json={"query": query, "variables": variables or {}},
        )
        resp.raise_for_status()
        return resp.json()


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.environ.get("FASTMCP_SERVER_HOST", "0.0.0.0"),
        port=int(os.environ.get("FASTMCP_SERVER_PORT", "8003")),
        # Serve exactly at /mcp/ — the gateway route dials this path, and
        # FastMCP 3's default (/mcp) would 307 it out of the route (same
        # trailing-slash rule as the docs/qdrant sidecars).
        path="/mcp/",
    )
