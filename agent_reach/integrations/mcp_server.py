# -*- coding: utf-8 -*-
"""
Agent Reach MCP Server — expose doctor/status as MCP tool.

Run: python -m agent_reach.integrations.mcp_server

Agent Reach is an installer + doctor tool. For actual reading/searching,
agents should call upstream tools directly (twitter-cli, yt-dlp, mcporter, etc.).
"""

import asyncio
import json
import sys

from agent_reach.config import Config
from agent_reach.core import AgentReach, strip_markup
from agent_reach.utils.text import scrub_url_credentials

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    HAS_MCP = True
except ImportError:
    HAS_MCP = False

MCP_INSTALL_HINT = (
    "MCP support is not installed. Install the optional extra: python -m pip install "
    "'agent-reach[mcp] @ https://github.com/hemal08ce094/agent-reach/archive/main.zip' "
    "(or: python -m pip install 'mcp[cli]>=1.0,<2')"
)

_STATUS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {
            "type": "string",
            "enum": ["text", "json"],
            "default": "text",
            "description": (
                "text: plain-text report (no terminal markup); json: the same "
                "per-channel data as `agent-reach doctor --json`"
            ),
        },
    },
}


def _status_text(eyes, output_format: str) -> str:
    """Build the get_status payload. Blocking; run it off the event loop."""
    if output_format == "json":
        return json.dumps(eyes.doctor(), ensure_ascii=False, indent=2)
    return strip_markup(eyes.doctor_report())


def create_server():
    if not HAS_MCP:
        print(MCP_INSTALL_HINT, file=sys.stderr)
        sys.exit(1)

    server = Server("agent-reach")
    config = Config(read_only=True)
    eyes = AgentReach(config)

    @server.list_tools()
    async def list_tools():
        return [
            Tool(name="get_status",
                 description=(
                     "Get Agent Reach status: which channels are installed and active. "
                     "Pass format=json for machine-readable per-channel data."
                 ),
                 inputSchema=_STATUS_INPUT_SCHEMA),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "get_status":
                output_format = (arguments or {}).get("format") or "text"
                if output_format not in ("text", "json"):
                    text = f"Unknown format: {output_format} (expected text or json)"
                else:
                    # Doctor probes upstream tools for seconds; never block the
                    # MCP event loop while it runs.
                    text = await asyncio.to_thread(_status_text, eyes, output_format)
            else:
                text = f"Unknown tool: {name}"
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error: {scrub_url_credentials(e)}",
                )
            ]

    return server


async def main():
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
