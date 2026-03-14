"""
Generic Memory MCP Server (Phase 1) — powered by mcp SDK.

Exposes 6 tools:
    1. memory_get          — read Markdown memory file content
    2. memory_search       — keyword-based memory search
    3. memory_guard_check  — run guard checks from config
    4. memory_backup       — backup memory files
    5. memory_compact      — rule-based memory compaction
    6. memory_write        — controlled write to memory files
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

from .memory_backup import backup_files
from .memory_compactor import compact_memory
from .memory_config import MemoryConfig, load_config
from .memory_guard import memory_guard_check
from .memory_reader import memory_get
from .memory_result import error_result
from .memory_search import memory_search
from .memory_writer import memory_write

logger = logging.getLogger(__name__)

SERVER_NAME = "generic-memory-mcp"
SERVER_VERSION = "0.2.0"

# ── Static base descriptions (functional semantics only) ────────────────

_BASE_DESCRIPTIONS: dict[str, str] = {
    "memory_get": (
        "Read memory file content with optional line range and truncation."
    ),
    "memory_search": (
        "Run keyword search across memory files with heading-weighted scoring and context windows."
    ),
    "memory_guard_check": (
        "Run capacity guard checks (per-file and total budget) from .ai-memory/config.json."
    ),
    "memory_backup": (
        "Backup memory files to .ai-memory/backups/ with auto-rotation. "
        "Recommended before compact or manual edits."
    ),
    "memory_compact": (
        "Rule-based compaction tool (default dry_run=true). "
        "Policies: hot_task (task context), error_summary (error context), "
        "warm_context (sprint focus — extracts sprint/focus/blockers/decisions headings only; "
        "do NOT use on structurally different files like progress.md). "
        "No LLM dependency."
    ),
    "memory_write": (
        "Write content to a memory file with safety controls. "
        "Supports overwrite and append modes. Auto-backup, atomic write, "
        "per-file guard + global budget check. Rejects write if total budget exceeded."
    ),
}


def _build_file_roles(config: MemoryConfig) -> str:
    """Build a dynamic file-roles suffix from config guard targets."""
    roles = []
    for t in config.guard_targets:
        if t.role:
            roles.append(f"{t.path} ({t.role})")
        else:
            roles.append(t.path)
    if roles:
        return " Available memory files: " + "; ".join(roles) + "."
    return ""


def _build_tools(config: MemoryConfig) -> list[Tool]:
    """Build tool definitions with dynamic descriptions from config."""
    file_roles = _build_file_roles(config)

    # Collect recommended paths from config targets
    target_paths = [t.path for t in config.guard_targets]
    path_hint = ", ".join(target_paths) if target_paths else "memory-bank/*.md, .ai-context/*.md"

    return [
        Tool(
            name="memory_get",
            description=_BASE_DESCRIPTIONS["memory_get"] + file_roles,
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": f"Target file path. Recommended: {path_hint}. Must stay within allowed_roots.",
                    },
                    "start_line": {"type": "integer", "minimum": 1, "description": "Start line (1-based, optional)"},
                    "end_line": {"type": "integer", "minimum": 1, "description": "End line (1-based, optional)"},
                    "max_chars": {"type": "integer", "minimum": 0, "description": "Max characters to return (optional)"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_search",
            description=_BASE_DESCRIPTIONS["memory_search"] + file_roles,
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword(s)"},
                    "scopes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Search scope directories, e.g. ['memory-bank'], ['.ai-context'].",
                    },
                    "top_k": {"type": "integer", "minimum": 1, "description": "Number of results to return (default 10)"},
                    "include_paths": {"type": "array", "items": {"type": "string"}, "description": "Include path globs"},
                    "exclude_paths": {"type": "array", "items": {"type": "string"}, "description": "Exclude path globs"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_guard_check",
            description=_BASE_DESCRIPTIONS["memory_guard_check"],
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_backup",
            description=_BASE_DESCRIPTIONS["memory_backup"],
            inputSchema={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": f"File paths to backup. Common targets: {path_hint}.",
                    },
                    "reason": {"type": "string", "description": "Backup reason (optional)"},
                    "tag": {"type": "string", "description": "Tag (optional)"},
                },
                "required": ["paths"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_compact",
            description=_BASE_DESCRIPTIONS["memory_compact"],
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target file path for compaction.",
                    },
                    "policy": {
                        "type": "string",
                        "enum": ["hot_task", "error_summary", "warm_context"],
                        "description": "Compaction policy: hot_task, error_summary, or warm_context",
                    },
                    "dry_run": {"type": "boolean", "default": True, "description": "Preview only; do not write files"},
                    "backup": {"type": "boolean", "default": True, "description": "Create backup before apply mode"},
                    "archive_original": {"type": "boolean", "default": True, "description": "Archive original content"},
                    "compress_to_tokens": {"type": "integer", "minimum": 1, "description": "Target token cap (optional)"},
                },
                "required": ["path", "policy"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_write",
            description=_BASE_DESCRIPTIONS["memory_write"] + file_roles,
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": f"Target file path. Must be within allowed_roots. Targets: {path_hint}.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write. For overwrite mode, this replaces the entire file.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "default": "overwrite",
                        "description": "Write mode: 'overwrite' replaces the file, 'append' adds to the end.",
                    },
                    "backup": {
                        "type": "boolean",
                        "default": True,
                        "description": "Auto-backup existing file before writing (default true).",
                    },
                    "create_if_missing": {
                        "type": "boolean",
                        "default": True,
                        "description": "Create the file if it does not exist (default true).",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for the write (logged in audit event, optional).",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        ),
    ]


# ── Tool dispatcher ─────────────────────────────────────────────────────

def _check_required(args: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    """Return error_result if any required key is missing from args, else None.

    Uses `k not in args or args[k] is None` to allow empty strings and zero values.
    """
    missing = [k for k in keys if k not in args or args[k] is None]
    if missing:
        return error_result("invalid_input", f"missing required parameter(s): {', '.join(missing)}")
    return None


def _dispatch_tool(config: MemoryConfig, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call and return the result dict."""
    try:
        if name == "memory_get":
            err = _check_required(args, "path")
            if err:
                return err
            return memory_get(
                config,
                path=str(args.get("path", "")),
                start_line=args.get("start_line"),
                end_line=args.get("end_line"),
                max_chars=args.get("max_chars"),
            )
        elif name == "memory_search":
            err = _check_required(args, "query")
            if err:
                return err
            return memory_search(
                config,
                query=str(args.get("query", "")),
                scopes=args.get("scopes"),
                top_k=args.get("top_k"),
                include_paths=args.get("include_paths"),
                exclude_paths=args.get("exclude_paths"),
            )
        elif name == "memory_guard_check":
            return memory_guard_check(config)
        elif name == "memory_backup":
            return backup_files(
                config,
                paths=args.get("paths", []),
                reason=args.get("reason"),
                tag=args.get("tag"),
            )
        elif name == "memory_compact":
            err = _check_required(args, "path", "policy")
            if err:
                return err
            return compact_memory(
                config,
                path=str(args.get("path", "")),
                policy=str(args.get("policy", "")),
                dry_run=bool(args.get("dry_run", True)),
                backup=bool(args.get("backup", True)),
                archive_original=bool(args.get("archive_original", True)),
                compress_to_tokens=args.get("compress_to_tokens"),
            )
        elif name == "memory_write":
            err = _check_required(args, "path", "content")
            if err:
                return err
            return memory_write(
                config,
                path=str(args.get("path", "")),
                content=str(args.get("content", "")),
                mode=str(args.get("mode", "overwrite")),
                backup=bool(args.get("backup", True)),
                create_if_missing=bool(args.get("create_if_missing", True)),
                reason=args.get("reason"),
            )
        else:
            return error_result("unknown_tool", f"unknown tool: {name}")
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return error_result("internal_error", f"{exc}")


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
