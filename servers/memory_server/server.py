"""
Generic Memory MCP Server (Phase 1) — powered by mcp SDK.

Exposes 3 default facade tools:
    1. memory_read    — read/search memory and runtime digests
    2. memory_write   — write files, records, observations, and artifact links
    3. memory_context — compile/read runtime context and trace lineage

Set mcp.expose_admin_tools=true in config to also expose legacy/admin tools.
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
from .memory_compiler import memory_compare_snapshots, memory_compile, memory_get_runtime_digest
from .memory_compactor import compact_memory
from .memory_config import MemoryConfig, load_config
from .memory_governance import memory_archive_record, memory_publish_candidate, memory_validate_candidate
from .memory_guard import memory_guard_check
from .memory_lineage import (
    memory_link_artifact,
    memory_list_conflicts,
    memory_record_observation,
    memory_trace_lineage,
)
from .memory_maintenance import memory_delete_record, memory_health_check, memory_migrate_records
from .memory_record_index import memory_rebuild_index, memory_search_records, memory_update_index
from .memory_reader import memory_get
from .memory_records import memory_write_record
from .memory_retrieval import memory_retrieve_context
from .memory_result import error_result
from .memory_search import memory_search
from .memory_writer import memory_write as memory_write_file

logger = logging.getLogger(__name__)

SERVER_NAME = "generic-memory-mcp"
SERVER_VERSION = "0.5.0"

# ── Static base descriptions (functional semantics only) ────────────────

_BASE_DESCRIPTIONS: dict[str, str] = {
    "memory_read": (
        "Facade read tool for runtime memory access. Supports file reads, file search, "
        "record search, and compiled runtime digest reads through an operation field."
    ),
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
        "Facade write tool for memory mutation. Supports ordinary file writes, "
        "structured record writes, observation capture, and artifact/facet linking "
        "through an operation field. Defaults to ordinary file write for compatibility."
    ),
    "memory_context": (
        "Facade context tool for AI runtime context. Supports deterministic compile, "
        "runtime digest reads, lineage tracing, conflict listing, snapshot comparison, "
        "and P3 context retrieval through an operation field."
    ),
    "memory_write_file": (
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
        "Supported targets include runtime digests, snapshots, review queues, rollback context, "
        "and dao/fa/shu digests. Defaults to compact body output. "
        "No LLM dependency."
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
    "memory_record_observation": (
        "Create a schema v2 observation record for raw evidence capture with optional artifact facets."
    ),
    "memory_link_artifact": (
        "Attach artifact/facet metadata to an existing record and upgrade it to schema v2 if needed."
    ),
    "memory_trace_lineage": (
        "Trace schema v2 lineage edges for a record through derived_from, supersedes, and conflicts_with."
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


def _build_facade_tools(file_roles: str, path_hint: str) -> list[Tool]:
    return [
        Tool(
            name="memory_read",
            description=_BASE_DESCRIPTIONS["memory_read"] + file_roles,
            inputSchema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["get", "search", "search_records", "runtime_digest"],
                        "default": "get",
                        "description": "Read operation to perform.",
                    },
                    "path": {
                        "type": "string",
                        "description": f"Target file path for operation=get. Recommended: {path_hint}.",
                    },
                    "query": {"type": "string", "description": "Search query for search operations."},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "max_chars": {"type": "integer", "minimum": 0},
                    "scopes": {"type": "array", "items": {"type": "string"}},
                    "top_k": {"type": "integer", "minimum": 1},
                    "include_paths": {"type": "array", "items": {"type": "string"}},
                    "exclude_paths": {"type": "array", "items": {"type": "string"}},
                    "user": {"type": "string"},
                    "task_id": {"type": "string"},
                    "branch": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_write",
            description=_BASE_DESCRIPTIONS["memory_write"] + file_roles,
            inputSchema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["file", "record", "observation", "link_artifact"],
                        "default": "file",
                        "description": "Write operation to perform. Defaults to file for legacy compatibility.",
                    },
                    "path": {"type": "string", "description": f"Target path for operation=file. Targets: {path_hint}."},
                    "content": {"type": "string", "description": "File content for operation=file."},
                    "content_markdown": {"type": "string", "description": "Markdown body for record/observation writes."},
                    "mode": {"type": "string", "enum": ["overwrite", "append"], "default": "overwrite"},
                    "backup": {"type": "boolean", "default": True},
                    "create_if_missing": {"type": "boolean", "default": True},
                    "reason": {"type": "string"},
                    "inject_user_tag": {"type": "boolean"},
                    "record_id": {"type": "string", "description": "Existing record id for operation=link_artifact."},
                    "schema_version": {"type": "string", "enum": ["1.0", "2.0"]},
                    "record_kind": {"type": "string"},
                    "scope": {"type": "string"},
                    "status": {"type": "string"},
                    "author": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "task_id": {"type": "string"},
                    "branch": {"type": "string"},
                    "validated_by": {"type": "string"},
                    "classifier_model": {"type": "string"},
                    "classifier_prompt_version": {"type": "string"},
                    "tag_schema_version": {"type": "string"},
                    "occurred_at": {"type": "string"},
                    "valid_from": {"type": "string"},
                    "valid_to": {"type": "string"},
                    "memory_tier": {"type": "string", "enum": ["hot", "warm", "cold", "fossil"]},
                    "cognitive_level": {"type": "string", "enum": ["dao", "fa", "shu"]},
                    "derived_from_record_ids": {"type": "array", "items": {"type": "string"}},
                    "derived_from_snapshot_ids": {"type": "array", "items": {"type": "string"}},
                    "derived_from_revision_ids": {"type": "array", "items": {"type": "string"}},
                    "supersedes": {"type": "array", "items": {"type": "string"}},
                    "conflicts_with": {"type": "array", "items": {"type": "string"}},
                    "related_artifact_ids": {"type": "array", "items": {"type": "string"}},
                    "importance_score": {"type": "number", "minimum": 0, "maximum": 1},
                    "asset_paths": {"type": "array", "items": {"type": "string"}},
                    "map_names": {"type": "array", "items": {"type": "string"}},
                    "plugin_names": {"type": "array", "items": {"type": "string"}},
                    "module_names": {"type": "array", "items": {"type": "string"}},
                    "class_names": {"type": "array", "items": {"type": "string"}},
                    "blueprint_paths": {"type": "array", "items": {"type": "string"}},
                    "system_area": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_context",
            description=_BASE_DESCRIPTIONS["memory_context"],
            inputSchema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "compile",
                            "runtime_digest",
                            "trace_lineage",
                            "list_conflicts",
                            "compare_snapshots",
                            "retrieve_context",
                        ],
                        "default": "compile",
                        "description": "Context operation to perform.",
                    },
                    "target": {
                        "type": "string",
                        "enum": [
                            "runtime_digest",
                            "task_handoff",
                            "system_digest",
                            "publish_queue",
                            "daily_snapshot",
                            "weekly_snapshot",
                            "monthly_snapshot",
                            "rollback_context",
                            "review_queue",
                            "dao_digest",
                            "fa_digest",
                            "shu_digest",
                        ],
                    },
                    "user": {"type": "string"},
                    "task_id": {"type": "string"},
                    "branch": {"type": "string"},
                    "include_scopes": {"type": "array", "items": {"type": "string"}},
                    "include_statuses": {"type": "array", "items": {"type": "string"}},
                    "preferred_tags": {"type": "array", "items": {"type": "string"}},
                    "body_mode": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
                    "as_of": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 0},
                    "record_id": {"type": "string"},
                    "max_depth": {"type": "integer", "minimum": 0},
                    "include_resolved": {"type": "boolean", "default": False},
                    "path": {"type": "string"},
                    "other_path": {"type": "string"},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1},
                    "window_start": {"type": "string"},
                    "window_end": {"type": "string"},
                    "system_area": {"type": "string"},
                    "asset_paths": {"type": "array", "items": {"type": "string"}},
                    "map_names": {"type": "array", "items": {"type": "string"}},
                    "plugin_names": {"type": "array", "items": {"type": "string"}},
                    "module_names": {"type": "array", "items": {"type": "string"}},
                    "class_names": {"type": "array", "items": {"type": "string"}},
                    "blueprint_paths": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        ),
    ]


def _build_tools(config: MemoryConfig) -> list[Tool]:
    """Build tool definitions with dynamic descriptions from config."""
    file_roles = _build_file_roles(config)

    # Collect recommended paths from config targets
    target_paths = [t.path for t in config.guard_targets]
    path_hint = ", ".join(target_paths) if target_paths else "memory-bank/*.md, .ai-context/*.md"
    facade_tools = _build_facade_tools(file_roles, path_hint)
    if not config.mcp_expose_admin_tools:
        return facade_tools

    legacy_tools = [
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
                            "observation",
                            "artifact_ref",
                            "incident",
                            "decision",
                            "procedure",
                            "snapshot_daily",
                            "snapshot_weekly",
                            "snapshot_monthly",
                        ],
                        "default": "note",
                    },
                    "scope": {
                        "type": "string",
                        "enum": [
                            "personal",
                            "shared",
                            "local",
                            "archive",
                            "session",
                            "user_private",
                            "task_or_branch",
                            "project_shared",
                            "org_shared",
                        ],
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
                    "schema_version": {"type": "string", "enum": ["1.0", "2.0"]},
                    "occurred_at": {"type": "string"},
                    "valid_from": {"type": "string"},
                    "valid_to": {"type": "string"},
                    "memory_tier": {"type": "string", "enum": ["hot", "warm", "cold", "fossil"]},
                    "cognitive_level": {"type": "string", "enum": ["dao", "fa", "shu"]},
                    "derived_from_record_ids": {"type": "array", "items": {"type": "string"}},
                    "derived_from_snapshot_ids": {"type": "array", "items": {"type": "string"}},
                    "derived_from_revision_ids": {"type": "array", "items": {"type": "string"}},
                    "supersedes": {"type": "array", "items": {"type": "string"}},
                    "conflicts_with": {"type": "array", "items": {"type": "string"}},
                    "related_artifact_ids": {"type": "array", "items": {"type": "string"}},
                    "importance_score": {"type": "number", "minimum": 0, "maximum": 1},
                    "asset_paths": {"type": "array", "items": {"type": "string"}},
                    "map_names": {"type": "array", "items": {"type": "string"}},
                    "plugin_names": {"type": "array", "items": {"type": "string"}},
                    "module_names": {"type": "array", "items": {"type": "string"}},
                    "class_names": {"type": "array", "items": {"type": "string"}},
                    "blueprint_paths": {"type": "array", "items": {"type": "string"}},
                    "system_area": {"type": "string"},
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
                        "enum": [
                            "runtime_digest",
                            "task_handoff",
                            "system_digest",
                            "publish_queue",
                            "daily_snapshot",
                            "weekly_snapshot",
                            "monthly_snapshot",
                            "rollback_context",
                            "review_queue",
                            "dao_digest",
                            "fa_digest",
                            "shu_digest",
                        ],
                        "description": "Compile target.",
                    },
                    "user": {"type": "string"},
                    "task_id": {"type": "string"},
                    "branch": {"type": "string"},
                    "include_scopes": {"type": "array", "items": {"type": "string"}},
                    "include_statuses": {"type": "array", "items": {"type": "string"}},
                    "preferred_tags": {"type": "array", "items": {"type": "string"}},
                    "as_of": {"type": "string"},
                    "body_mode": {
                        "type": "string",
                        "enum": ["compact", "full"],
                        "default": "compact",
                        "description": (
                            "Compiled body rendering mode. compact keeps only key extracted content "
                            "plus source references; full preserves previous verbose record rendering."
                        ),
                    },
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
        Tool(
            name="memory_record_observation",
            description=_BASE_DESCRIPTIONS["memory_record_observation"],
            inputSchema={
                "type": "object",
                "properties": {
                    "content_markdown": {"type": "string", "description": "Observation body as Markdown."},
                    "author": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "task_id": {"type": "string"},
                    "branch": {"type": "string"},
                    "occurred_at": {"type": "string"},
                    "memory_tier": {"type": "string", "enum": ["hot", "warm", "cold", "fossil"], "default": "hot"},
                    "cognitive_level": {"type": "string", "enum": ["dao", "fa", "shu"], "default": "shu"},
                    "related_artifact_ids": {"type": "array", "items": {"type": "string"}},
                    "asset_paths": {"type": "array", "items": {"type": "string"}},
                    "map_names": {"type": "array", "items": {"type": "string"}},
                    "plugin_names": {"type": "array", "items": {"type": "string"}},
                    "module_names": {"type": "array", "items": {"type": "string"}},
                    "class_names": {"type": "array", "items": {"type": "string"}},
                    "blueprint_paths": {"type": "array", "items": {"type": "string"}},
                    "system_area": {"type": "string"},
                },
                "required": ["content_markdown"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_link_artifact",
            description=_BASE_DESCRIPTIONS["memory_link_artifact"],
            inputSchema={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "related_artifact_ids": {"type": "array", "items": {"type": "string"}},
                    "asset_paths": {"type": "array", "items": {"type": "string"}},
                    "map_names": {"type": "array", "items": {"type": "string"}},
                    "plugin_names": {"type": "array", "items": {"type": "string"}},
                    "module_names": {"type": "array", "items": {"type": "string"}},
                    "class_names": {"type": "array", "items": {"type": "string"}},
                    "blueprint_paths": {"type": "array", "items": {"type": "string"}},
                    "system_area": {"type": "string"},
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_trace_lineage",
            description=_BASE_DESCRIPTIONS["memory_trace_lineage"],
            inputSchema={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "max_depth": {"type": "integer", "minimum": 0},
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
        ),
    ]
    return facade_tools + [tool for tool in legacy_tools if tool.name not in {"memory_write"}]


# ── Tool dispatcher ─────────────────────────────────────────────────────

def _check_required(args: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    """Return error_result if any required key is missing from args, else None.

    Uses `k not in args or args[k] is None` to allow empty strings and zero values.
    """
    missing = [k for k in keys if k not in args or args[k] is None]
    if missing:
        return error_result("invalid_input", f"missing required parameter(s): {', '.join(missing)}")
    return None


def _dispatch_memory_read(config: MemoryConfig, args: dict[str, Any]) -> dict[str, Any]:
    operation = str(args.get("operation") or ("search" if args.get("query") else "get"))
    if operation == "get":
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
    if operation == "search":
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
    if operation == "search_records":
        err = _check_required(args, "query")
        if err:
            return err
        return memory_search_records(config, query=str(args.get("query", "")), top_k=args.get("top_k"))
    if operation == "runtime_digest":
        return memory_get_runtime_digest(
            config,
            user=str(args["user"]) if args.get("user") is not None else None,
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            max_chars=args.get("max_chars"),
        )
    return error_result("invalid_input", "operation must be one of: get, search, search_records, runtime_digest")


def _dispatch_memory_write(config: MemoryConfig, args: dict[str, Any]) -> dict[str, Any]:
    if args.get("operation") is not None:
        operation = str(args.get("operation"))
    elif args.get("content_markdown") is not None:
        operation = "record"
    elif args.get("record_id") is not None:
        operation = "link_artifact"
    else:
        operation = "file"

    if operation == "file":
        err = _check_required(args, "path", "content")
        if err:
            return err
        return memory_write_file(
            config,
            path=str(args.get("path", "")),
            content=str(args.get("content", "")),
            mode=str(args.get("mode", "overwrite")),
            backup=bool(args.get("backup", True)),
            create_if_missing=bool(args.get("create_if_missing", True)),
            reason=args.get("reason"),
            inject_user_tag=args.get("inject_user_tag"),
        )
    if operation == "record":
        err = _check_required(args, "content_markdown")
        if err:
            return err
        return memory_write_record(
            config,
            content_markdown=str(args.get("content_markdown", "")),
            schema_version=str(args["schema_version"]) if args.get("schema_version") is not None else None,
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
            occurred_at=str(args["occurred_at"]) if args.get("occurred_at") is not None else None,
            valid_from=str(args["valid_from"]) if args.get("valid_from") is not None else None,
            valid_to=str(args["valid_to"]) if args.get("valid_to") is not None else None,
            memory_tier=str(args["memory_tier"]) if args.get("memory_tier") is not None else None,
            cognitive_level=str(args["cognitive_level"]) if args.get("cognitive_level") is not None else None,
            derived_from_record_ids=args.get("derived_from_record_ids"),
            derived_from_snapshot_ids=args.get("derived_from_snapshot_ids"),
            derived_from_revision_ids=args.get("derived_from_revision_ids"),
            supersedes=args.get("supersedes"),
            conflicts_with=args.get("conflicts_with"),
            related_artifact_ids=args.get("related_artifact_ids"),
            importance_score=args.get("importance_score"),
            asset_paths=args.get("asset_paths"),
            map_names=args.get("map_names"),
            plugin_names=args.get("plugin_names"),
            module_names=args.get("module_names"),
            class_names=args.get("class_names"),
            blueprint_paths=args.get("blueprint_paths"),
            system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
        )
    if operation == "observation":
        err = _check_required(args, "content_markdown")
        if err:
            return err
        return memory_record_observation(
            config,
            content_markdown=str(args.get("content_markdown", "")),
            author=str(args["author"]) if args.get("author") is not None else None,
            tags=args.get("tags"),
            confidence=args.get("confidence"),
            source_refs=args.get("source_refs"),
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            occurred_at=str(args["occurred_at"]) if args.get("occurred_at") is not None else None,
            memory_tier=str(args["memory_tier"]) if args.get("memory_tier") is not None else "hot",
            cognitive_level=str(args["cognitive_level"]) if args.get("cognitive_level") is not None else "shu",
            related_artifact_ids=args.get("related_artifact_ids"),
            asset_paths=args.get("asset_paths"),
            map_names=args.get("map_names"),
            plugin_names=args.get("plugin_names"),
            module_names=args.get("module_names"),
            class_names=args.get("class_names"),
            blueprint_paths=args.get("blueprint_paths"),
            system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
        )
    if operation == "link_artifact":
        err = _check_required(args, "record_id")
        if err:
            return err
        return memory_link_artifact(
            config,
            str(args.get("record_id", "")),
            related_artifact_ids=args.get("related_artifact_ids"),
            asset_paths=args.get("asset_paths"),
            map_names=args.get("map_names"),
            plugin_names=args.get("plugin_names"),
            module_names=args.get("module_names"),
            class_names=args.get("class_names"),
            blueprint_paths=args.get("blueprint_paths"),
            system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
        )
    return error_result("invalid_input", "operation must be one of: file, record, observation, link_artifact")


def _dispatch_memory_context(config: MemoryConfig, args: dict[str, Any]) -> dict[str, Any]:
    operation = str(args.get("operation") or "compile")
    if operation == "compile":
        return memory_compile(
            config,
            target=str(args.get("target", "runtime_digest")),
            user=str(args["user"]) if args.get("user") is not None else None,
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            include_scopes=args.get("include_scopes"),
            include_statuses=args.get("include_statuses"),
            preferred_tags=args.get("preferred_tags"),
            body_mode=str(args["body_mode"]) if args.get("body_mode") is not None else None,
            as_of=str(args["as_of"]) if args.get("as_of") is not None else None,
        )
    if operation == "runtime_digest":
        return memory_get_runtime_digest(
            config,
            user=str(args["user"]) if args.get("user") is not None else None,
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            max_chars=args.get("max_chars"),
        )
    if operation == "trace_lineage":
        err = _check_required(args, "record_id")
        if err:
            return err
        return memory_trace_lineage(config, str(args.get("record_id", "")), max_depth=args.get("max_depth"))
    if operation == "list_conflicts":
        return memory_list_conflicts(config, include_resolved=bool(args.get("include_resolved", False)))
    if operation == "compare_snapshots":
        err = _check_required(args, "path", "other_path")
        if err:
            return err
        return memory_compare_snapshots(
            config,
            path=str(args.get("path", "")),
            other_path=str(args.get("other_path", "")),
        )
    if operation == "retrieve_context":
        return memory_retrieve_context(
            config,
            query=str(args["query"]) if args.get("query") is not None else None,
            user=str(args["user"]) if args.get("user") is not None else None,
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            include_scopes=args.get("include_scopes"),
            include_statuses=args.get("include_statuses"),
            preferred_tags=args.get("preferred_tags"),
            window_start=str(args["window_start"]) if args.get("window_start") is not None else None,
            window_end=str(args["window_end"]) if args.get("window_end") is not None else None,
            system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
            asset_paths=args.get("asset_paths"),
            map_names=args.get("map_names"),
            plugin_names=args.get("plugin_names"),
            module_names=args.get("module_names"),
            class_names=args.get("class_names"),
            blueprint_paths=args.get("blueprint_paths"),
            top_k=args.get("top_k"),
        )
    return error_result(
        "invalid_input",
        "operation must be one of: compile, runtime_digest, trace_lineage, list_conflicts, compare_snapshots, retrieve_context",
    )


def _dispatch_tool(config: MemoryConfig, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call and return the result dict."""
    try:
        if name == "memory_read":
            return _dispatch_memory_read(config, args)
        elif name == "memory_context":
            return _dispatch_memory_context(config, args)
        elif name == "memory_get":
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
            return _dispatch_memory_write(config, args)
        elif name == "memory_write_record":
            err = _check_required(args, "content_markdown")
            if err:
                return err
            return memory_write_record(
                config,
                content_markdown=str(args.get("content_markdown", "")),
                schema_version=str(args["schema_version"]) if args.get("schema_version") is not None else None,
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
                occurred_at=str(args["occurred_at"]) if args.get("occurred_at") is not None else None,
                valid_from=str(args["valid_from"]) if args.get("valid_from") is not None else None,
                valid_to=str(args["valid_to"]) if args.get("valid_to") is not None else None,
                memory_tier=str(args["memory_tier"]) if args.get("memory_tier") is not None else None,
                cognitive_level=str(args["cognitive_level"]) if args.get("cognitive_level") is not None else None,
                derived_from_record_ids=args.get("derived_from_record_ids"),
                derived_from_snapshot_ids=args.get("derived_from_snapshot_ids"),
                derived_from_revision_ids=args.get("derived_from_revision_ids"),
                supersedes=args.get("supersedes"),
                conflicts_with=args.get("conflicts_with"),
                related_artifact_ids=args.get("related_artifact_ids"),
                importance_score=args.get("importance_score"),
                asset_paths=args.get("asset_paths"),
                map_names=args.get("map_names"),
                plugin_names=args.get("plugin_names"),
                module_names=args.get("module_names"),
                class_names=args.get("class_names"),
                blueprint_paths=args.get("blueprint_paths"),
                system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
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
                body_mode=str(args["body_mode"]) if args.get("body_mode") is not None else None,
                as_of=str(args["as_of"]) if args.get("as_of") is not None else None,
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
        elif name == "memory_record_observation":
            err = _check_required(args, "content_markdown")
            if err:
                return err
            return memory_record_observation(
                config,
                content_markdown=str(args.get("content_markdown", "")),
                author=str(args["author"]) if args.get("author") is not None else None,
                tags=args.get("tags"),
                confidence=args.get("confidence"),
                source_refs=args.get("source_refs"),
                task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
                branch=str(args["branch"]) if args.get("branch") is not None else None,
                occurred_at=str(args["occurred_at"]) if args.get("occurred_at") is not None else None,
                memory_tier=str(args["memory_tier"]) if args.get("memory_tier") is not None else "hot",
                cognitive_level=str(args["cognitive_level"]) if args.get("cognitive_level") is not None else "shu",
                related_artifact_ids=args.get("related_artifact_ids"),
                asset_paths=args.get("asset_paths"),
                map_names=args.get("map_names"),
                plugin_names=args.get("plugin_names"),
                module_names=args.get("module_names"),
                class_names=args.get("class_names"),
                blueprint_paths=args.get("blueprint_paths"),
                system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
            )
        elif name == "memory_link_artifact":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_link_artifact(
                config,
                str(args.get("record_id", "")),
                related_artifact_ids=args.get("related_artifact_ids"),
                asset_paths=args.get("asset_paths"),
                map_names=args.get("map_names"),
                plugin_names=args.get("plugin_names"),
                module_names=args.get("module_names"),
                class_names=args.get("class_names"),
                blueprint_paths=args.get("blueprint_paths"),
                system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
            )
        elif name == "memory_trace_lineage":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_trace_lineage(
                config,
                str(args.get("record_id", "")),
                max_depth=args.get("max_depth"),
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
