"""The hermes-studio unit turns off every Studio startup action that writes
into Hermes: its own gateway, gateway recovery and MCP server injection into
profile configs. Runs bare or under pytest."""
from pathlib import Path

UNIT = (Path(__file__).resolve().parents[2]
        / "roles/hermes_agent/templates/hermes-studio.service.j2").read_text()


def test_studio_never_writes_its_mcp_servers_or_runs_a_gateway():
    for switch in ("HERMES_WEB_UI_DISABLE_GATEWAY_AUTOSTART",
                   "HERMES_WEB_UI_DISABLE_GATEWAY_STARTUP_RECOVERY",
                   "HERMES_WEB_UI_DISABLE_MCP_AUTOINJECT"):
        assert f"Environment={switch}=1" in UNIT, switch


if __name__ == "__main__":
    test_studio_never_writes_its_mcp_servers_or_runs_a_gateway()
