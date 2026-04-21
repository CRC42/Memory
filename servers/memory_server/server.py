"""
Generic Memory MCP Server (Phase 1) — powered by mcp SDK.

Exposes 18 tools:
    1. memory_get          — read Markdown memory file content
    2. memory_search       — keyword-based memory search
    3. memory_guard_check  — run guard checks from config
    4. memory_backup       — backup memory files
    5. memory_compact      — rule-based memory compaction
    6. memory_write        — controlled write to memory files
    7. memory_write_record — write structured Markdown + Front Matter records
    8. memory_rebuild_index — rebuild SQLite FTS index for records
    9. memory_search_records — search structured records through SQLite FTS
    10. memory_compile — compile records into rebuildable runtime views
    11. memory_get_runtime_digest — read compiled runtime digest
    12. memory_validate_candidate — validate a candidate record
    13. memory_publish_candidate — publish a validated candidate
    14. memory_archive_record — archive a record
    15. memory_update_index — incrementally update SQLite FTS for selected records
    16. memory_health_check — lint memory records and derived infrastructure
    17. memory_migrate_records — migrate record schema metadata
    18. memory_delete_record — delete archived records with tombstones
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
from .memory_compiler import memory_compile, memory_get_runtime_digest
from .memory_compactor import compact_memory
from .memory_config import MemoryConfig, load_config
from .memory_governance import memory_archive_record, memory_publish_candidate, memory_validate_candidate
from .memory_guard import memory_guard_check
from .memory_maintenance import memory_delete_record, memory_health_check, memory_migrate_records
from .memory_record_index import memory_rebuild_index, memory_search_records, memory_update_index
from .memory_reader import memory_get
from .memory_records import memory_write_record
from .memory_result import error_result
from .memory_search import memory_search
from .memory_writer import memory_write

logger = logging.getLogger(__name__)

SERVER_NAME = "generic-memory-mcp"
SERVER_VERSION = "0.4.0"

# ── Static base descriptions (functional semantics only) ────────────────

_BASE_DESCRIPTIONS: dict[str, str] = {
    "memory_get": (
        "Read memory file content with optional line range and truncation. "
        "When multi_user is enabled, user_scoped paths (e.g. activeContext.md) "
        "are automatically redirected to the per-user file (activeContext/{user}.md)."
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
        "per-file guard + global budget check. Rejects write if total budget exceeded. "
        "Multi-user: user_scoped paths auto-redirect to per-user files; "
        "append_only paths force overwrite→append downgrade; "
        "all writes include user identity tags for traceability."
    ),
    "memory_write_record": (
        "Write a structured memory record as Markdown + YAML Front Matter. "
        "This is the record-level vNext entry point and keeps existing file-level tools unchanged."
    ),
    "memory_rebuild_index": (
        "Rebuild .ai-memory/search.db from Markdown + Front Matter memory records. "
        "The SQLite FTS index is derived data and can be recreated at any time."
    ),
    "memory_search_records": (
        "Search structured memory records through the rebuilt SQLite FTS index. "
        "If the index does not exist, it is rebuilt first."
    ),
    "memory_compile": (
        "Compile structured memory records into deterministic Markdown runtime views. "
        "Supported targets: runtime_digest and task_handoff. No LLM dependency."
    ),
    "memory_get_runtime_digest": (
        "Read an existing compiled runtime digest. "
        "Run memory_compile first if the digest does not exist."
    ),
    "memory_validate_candidate": (
        "Validate a candidate memory record and move it from candidates into its governed record layer."
    ),
    "memory_publish_candidate": (
        "Publish a validated candidate into shared system memory. "
        "Requires status=validated and validated_by."
    ),
    "memory_archive_record": (
        "Archive a memory record into memory-bank/archive with traceable archive metadata."
    ),
    "memory_update_index": (
        "Incrementally update .ai-memory/search.db for selected record paths."
    ),
    "memory_health_check": (
        "Run record metadata lint checks and derived infrastructure health checks."
    ),
    "memory_migrate_records": (
        "Migrate record metadata to a target schema_version without changing record bodies."
    ),
    "memory_delete_record": (
        "Delete an archived record and write a tombstone. Non-archived records are rejected."
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
                    "inject_user_tag": {
                        "type": "boolean",
                        "description": (
                            "Whether to inject an HTML comment with the writing user and timestamp. "
                            "Omit (default) to auto-detect: Markdown files get the tag, every other "
                            "extension is left untouched so JSON/YAML/TOML/source files are not corrupted. "
                            "Set to false to force-disable, true to force-enable."
                        ),
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_write_record",
            description=_BASE_DESCRIPTIONS["memory_write_record"],
            inputSchema={
                "type": "object",
                "properties": {
                    "content_markdown": {
                        "type": "string",
                        "description": "Markdown body for the memory record.",
                    },
                    "record_kind": {
                        "type": "string",
                        "enum": [
                            "note",
                            "event",
                            "claim_candidate",
                            "rule_candidate",
                            "handoff",
                            "skill_candidate",
                            "validation_result",
                            "system_rule",
                            "archive_record",
                        ],
                        "default": "note",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["personal", "shared", "local", "archive"],
                        "default": "personal",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["raw", "candidate", "validated", "published", "degraded", "archived"],
                    },
                    "author": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "task_id": {"type": "string"},
                    "branch": {"type": "string"},
                    "validated_by": {"type": "string"},
                    "classifier_model": {"type": "string"},
                    "classifier_prompt_version": {"type": "string"},
                    "tag_schema_version": {"type": "string", "default": "v1"},
                },
                "required": ["content_markdown"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_rebuild_index",
            description=_BASE_DESCRIPTIONS["memory_rebuild_index"],
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_search_records",
            description=_BASE_DESCRIPTIONS["memory_search_records"],
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Record search query."},
                    "top_k": {"type": "integer", "minimum": 1, "description": "Number of results to return."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_compile",
            description=_BASE_DESCRIPTIONS["memory_compile"],
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["runtime_digest", "task_handoff", "system_digest", "publish_queue"],
                        "description": "Compile target.",
                    },
                    "user": {"type": "string"},
                    "task_id": {"type": "string"},
                    "branch": {"type": "string"},
                    "include_scopes": {"type": "array", "items": {"type": "string"}},
                    "include_statuses": {"type": "array", "items": {"type": "string"}},
                    "preferred_tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_get_runtime_digest",
            description=_BASE_DESCRIPTIONS["memory_get_runtime_digest"],
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {"type": "string"},
                    "task_id": {"type": "string"},
                    "branch": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_validate_candidate",
            description=_BASE_DESCRIPTIONS["memory_validate_candidate"],
            inputSchema={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "validated_by": {"type": "string"},
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_publish_candidate",
            description=_BASE_DESCRIPTIONS["memory_publish_candidate"],
            inputSchema={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "published_by": {"type": "string"},
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_archive_record",
            description=_BASE_DESCRIPTIONS["memory_archive_record"],
            inputSchema={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_update_index",
            description=_BASE_DESCRIPTIONS["memory_update_index"],
            inputSchema={
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
                "required": ["paths"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_health_check",
            description=_BASE_DESCRIPTIONS["memory_health_check"],
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_migrate_records",
            description=_BASE_DESCRIPTIONS["memory_migrate_records"],
            inputSchema={
                "type": "object",
                "properties": {
                    "target_schema_version": {"type": "string", "default": "1.0"},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_delete_record",
            description=_BASE_DESCRIPTIONS["memory_delete_record"],
            inputSchema={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["record_id"],
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
                inject_user_tag=args.get("inject_user_tag"),
            )
        elif name == "memory_write_record":
            err = _check_required(args, "content_markdown")
            if err:
                return err
            return memory_write_record(
                config,
                content_markdown=str(args.get("content_markdown", "")),
                record_kind=str(args.get("record_kind", "note")),
                scope=str(args.get("scope", "personal")),
                status=str(args["status"]) if args.get("status") is not None else None,
                author=str(args["author"]) if args.get("author") is not None else None,
                tags=args.get("tags"),
                confidence=args.get("confidence"),
                source_refs=args.get("source_refs"),
                task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
                branch=str(args["branch"]) if args.get("branch") is not None else None,
                validated_by=str(args["validated_by"]) if args.get("validated_by") is not None else None,
                classifier_model=str(args["classifier_model"]) if args.get("classifier_model") is not None else None,
                classifier_prompt_version=(
                    str(args["classifier_prompt_version"])
                    if args.get("classifier_prompt_version") is not None
                    else None
                ),
                tag_schema_version=str(args.get("tag_schema_version", "v1")),
            )
        elif name == "memory_rebuild_index":
            return memory_rebuild_index(config)
        elif name == "memory_search_records":
            err = _check_required(args, "query")
            if err:
                return err
            return memory_search_records(
                config,
                query=str(args.get("query", "")),
                top_k=args.get("top_k"),
            )
        elif name == "memory_compile":
            err = _check_required(args, "target")
            if err:
                return err
            return memory_compile(
                config,
                target=str(args.get("target", "")),
                user=str(args["user"]) if args.get("user") is not None else None,
                task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
                branch=str(args["branch"]) if args.get("branch") is not None else None,
                include_scopes=args.get("include_scopes"),
                include_statuses=args.get("include_statuses"),
                preferred_tags=args.get("preferred_tags"),
            )
        elif name == "memory_get_runtime_digest":
            return memory_get_runtime_digest(
                config,
                user=str(args["user"]) if args.get("user") is not None else None,
                task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
                branch=str(args["branch"]) if args.get("branch") is not None else None,
                max_chars=args.get("max_chars"),
            )
        elif name == "memory_validate_candidate":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_validate_candidate(
                config,
                str(args.get("record_id", "")),
                validated_by=str(args["validated_by"]) if args.get("validated_by") is not None else None,
            )
        elif name == "memory_publish_candidate":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_publish_candidate(
                config,
                str(args.get("record_id", "")),
                published_by=str(args["published_by"]) if args.get("published_by") is not None else None,
            )
        elif name == "memory_archive_record":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_archive_record(
                config,
                str(args.get("record_id", "")),
                reason=str(args["reason"]) if args.get("reason") is not None else None,
            )
        elif name == "memory_update_index":
            err = _check_required(args, "paths")
            if err:
                return err
            return memory_update_index(config, paths=args.get("paths", []))
        elif name == "memory_health_check":
            return memory_health_check(config)
        elif name == "memory_migrate_records":
            return memory_migrate_records(
                config,
                target_schema_version=str(args.get("target_schema_version", "1.0")),
            )
        elif name == "memory_delete_record":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_delete_record(
                config,
                str(args.get("record_id", "")),
                reason=str(args["reason"]) if args.get("reason") is not None else None,
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
