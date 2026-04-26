"""End-to-end MCP-protocol tests for the memory server.

Exercises the *real* `mcp.server.Server` instance produced by `create_server`
by dispatching `ListToolsRequest` and `CallToolRequest` through the SDK's
registered request-handler chain. This validates:

- `create_server(config)` wires up `list_tools` and `call_tool` correctly.
- Facade tools (`memory_read`, `memory_write`, `memory_context`, `memory_enhance`) are reachable
  via the actual MCP request envelope.
- Tool responses are JSON-serialised into a single `TextContent` block whose
  body parses back to the same dict the dispatch layer produced.
- `expose_admin_tools=true` makes legacy tools (e.g. `memory_get`) reachable
  end-to-end via the MCP envelope.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    ListToolsRequest,
)

from servers.memory_server.memory_config import load_config
from servers.memory_server.server import create_server


def _load_with_admin(repo: Path, expose_admin: bool):
    cfg_path = repo / ".ai-memory" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"mcp": {"expose_admin_tools": expose_admin}}), encoding="utf-8")
    return load_config(str(repo), str(cfg_path))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _list_tools(server) -> list[Any]:
    handler = server.request_handlers[ListToolsRequest]
    req = ListToolsRequest(method="tools/list")
    server_result = await handler(req)
    # ServerResult wraps the actual ListToolsResult under .root
    return server_result.root.tools


async def _call_tool(server, name: str, arguments: dict) -> dict:
    handler = server.request_handlers[CallToolRequest]
    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=name, arguments=arguments),
    )
    server_result = await handler(req)
    payload = server_result.root  # CallToolResult
    assert payload.content, "MCP response missing content"
    text_block = payload.content[0]
    assert getattr(text_block, "type", None) == "text"
    return json.loads(text_block.text)


def test_mcp_list_tools_default_facade(repo: Path) -> None:
    """Default config exposes exactly the 4 facade tools via MCP envelope."""
    config = load_config(repo)
    server = create_server(config)
    tools = _run(_list_tools(server))
    names = sorted(t.name for t in tools)
    assert names == ["memory_context", "memory_enhance", "memory_read", "memory_write"]


def test_mcp_list_tools_admin_mode(repo: Path) -> None:
    """expose_admin_tools=true exposes 24 unique tools (4 facades + 20 legacy)."""
    config = _load_with_admin(repo, True)
    server = create_server(config)
    tools = _run(_list_tools(server))
    names = [t.name for t in tools]
    assert len(names) == 24, f"expected 24 tools, got {len(names)}: {names}"
    assert len(set(names)) == 24, "tool names must be unique"
    assert (
        "memory_read" in names
        and "memory_write" in names
        and "memory_context" in names
        and "memory_enhance" in names
    )
    assert "memory_get" in names  # legacy admin tool


def test_mcp_call_memory_read_get(repo: Path) -> None:
    """memory_read{operation=get} round-trip via MCP envelope returns file content."""
    config = load_config(repo)
    server = create_server(config)
    result = _run(_call_tool(server, "memory_read", {
        "operation": "get",
        "path": "memory-bank/notes.md",
    }))
    assert result["ok"] is True, result
    assert "Boss Notes" in result["content"]


def test_mcp_call_memory_write_append(repo: Path) -> None:
    """memory_write{mode=append} via MCP envelope handles empty content."""
    config = load_config(repo)
    server = create_server(config)
    result = _run(_call_tool(server, "memory_write", {
        "path": "memory-bank/notes.md",
        "content": "",
        "mode": "append",
    }))
    assert result["ok"] is True, result
    assert result["mode"] == "append"


def test_mcp_call_memory_context_compile(repo: Path) -> None:
    """memory_context{operation=compile} via MCP envelope returns target list."""
    config = load_config(repo)
    server = create_server(config)
    result = _run(_call_tool(server, "memory_context", {
        "operation": "compile",
    }))
    assert result["ok"] is True, result
    # compile returns a runtime digest; check key shape rather than a single field name
    assert "included_record_ids" in result
    assert isinstance(result["content"], str) and result["content"]


def test_mcp_call_unknown_tool_returns_error(repo: Path) -> None:
    """Unknown tool name yields an `unknown_tool` error envelope, not an exception."""
    config = load_config(repo)
    server = create_server(config)
    result = _run(_call_tool(server, "no_such_tool", {}))
    assert result["ok"] is False
    assert result["error"] == "unknown_tool"
    assert "unknown tool" in result["message"].lower()


def test_mcp_call_legacy_tool_when_exposed(repo: Path) -> None:
    """When admin tools are exposed, legacy `memory_get` is callable end-to-end."""
    config = _load_with_admin(repo, True)
    server = create_server(config)
    result = _run(_call_tool(server, "memory_get", {"path": "memory-bank/notes.md"}))
    assert result["ok"] is True
    assert "Boss Notes" in result["content"]
