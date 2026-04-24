"""
Generic Memory MCP Server (Phase 1) — powered by mcp SDK.

Exposes 3 default facade tools:
    1. memory_read    — read/search memory and runtime digests
    2. memory_write   — write files, records, observations, and artifact links
    3. memory_context — compile/read runtime context and trace lineage

Set mcp.expose_admin_tools=true in config to also expose legacy/admin tools.

Internal layout (P1-A split):
    - server_descriptions.py : SERVER_NAME / SERVER_VERSION / _BASE_DESCRIPTIONS
    - server_tools.py        : _build_file_roles / _build_facade_tools / _build_tools
    - server_dispatch.py     : _check_required / _dispatch_memory_* / _dispatch_tool
    - server.py (this file)  : create_server / _run / main + back-compat re-exports
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .memory_config import MemoryConfig, load_config

# Back-compat re-exports for tests and external callers that still import
# from `servers.memory_server.server`. New code should import from the
# server_descriptions / server_tools / server_dispatch modules directly.
from .server_descriptions import SERVER_NAME, SERVER_VERSION, _BASE_DESCRIPTIONS  # noqa: F401
from .server_dispatch import (  # noqa: F401
    _check_required,
    _dispatch_memory_context,
    _dispatch_memory_read,
    _dispatch_memory_write,
    _dispatch_tool,
)
from .server_tools import (  # noqa: F401
    _build_facade_tools,
    _build_file_roles,
    _build_legacy_tools,
    _build_tools,
)

logger = logging.getLogger(__name__)


# ── Server setup ────────────────────────────────────────────────────────


def create_server(config: MemoryConfig) -> Server:
    """Create and configure the MCP Server instance."""
    server = Server(SERVER_NAME)
    tools = _build_tools(config)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        result = _dispatch_tool(config, name, arguments or {})
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return [TextContent(type="text", text=text)]

    return server


# ── Entry point ─────────────────────────────────────────────────────────


async def _run(config: MemoryConfig) -> None:
    server = create_server(config)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 memory MCP server")
    parser.add_argument("--root", default=os.getcwd(), help="Workspace root path")
    parser.add_argument("--config", default=None, help="Optional config path (default: .ai-memory/config.json)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    config = load_config(args.root, args.config)
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        logger.info("memory-mcp stopped by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
