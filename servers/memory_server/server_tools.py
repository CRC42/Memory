"""Tool schema builder for the memory MCP server.

Extracted from `server.py` (P1-A). Pure schema construction; no dispatch.
The tool list returned by `_build_tools` is what FastMCP advertises to
clients. Both the default 3-facade list and the legacy/admin list live here.
"""

from __future__ import annotations

from mcp.types import Tool

from .memory_config import MemoryConfig
from .server_descriptions import _BASE_DESCRIPTIONS


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
                    "distill": {
                        "type": "boolean",
                        "default": False,
                        "description": "Opt-in: after writing the raw record, run an LLM map-reduce distill and persist the summary as a derived `distilled_summary` record (derived_from_record_ids → raw id). Result attached as `distilled` on the response. Requires LLM config; on missing config returns `llm_unavailable` without losing the primary write.",
                    },
                    "distill_user_instruction": {
                        "type": "string",
                        "description": "Optional instruction prepended to the distillation user message (e.g. 'Focus on action items').",
                    },
                    "distill_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags to apply to the derived distilled record (defaults to `tags`).",
                    },
                    "distill_max_tokens": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Per-call max output tokens for the distill pass (clamped by LLMConfig.max_output_tokens_per_call).",
                    },
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
                            "important_memories",
                            "config_diagnose",
                            "rebuild_key_documents",
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
                    "max_tokens": {"type": "integer", "minimum": 0},
                    "max_items": {"type": "integer", "minimum": 1},
                    "window_start": {"type": "string"},
                    "window_end": {"type": "string"},
                    "system_area": {"type": "string"},
                    "asset_paths": {"type": "array", "items": {"type": "string"}},
                    "map_names": {"type": "array", "items": {"type": "string"}},
                    "plugin_names": {"type": "array", "items": {"type": "string"}},
                    "module_names": {"type": "array", "items": {"type": "string"}},
                    "class_names": {"type": "array", "items": {"type": "string"}},
                    "blueprint_paths": {"type": "array", "items": {"type": "string"}},
                    "summarize": {
                        "type": "boolean",
                        "default": False,
                        "description": "Opt-in (operation=retrieve_context): after deterministic recall, run an LLM map-reduce summary over the returned records (read-only, never writes back). Result attached as `summary` on the response.",
                    },
                    "summary_query": {
                        "type": "string",
                        "description": "Optional alternate question used to frame the summary (defaults to `query`).",
                    },
                    "summary_max_tokens": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Per-call max output tokens for the recall summary (clamped by LLMConfig.max_output_tokens_per_call).",
                    },
                    "summary_max_chars_per_record": {
                        "type": "integer",
                        "minimum": 256,
                        "default": 4000,
                        "description": "Truncate each record body to this many characters before feeding the LLM (cost guard for huge corpora).",
                    },
                    "targets": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["activeContext", "progress", "techContext", "systemPatterns"],
                        },
                        "description": "Used by operation=rebuild_key_documents. Subset of key documents to rebuild; omit to rebuild all four.",
                    },
                    "renderer": {
                        "type": "string",
                        "enum": ["deterministic", "auto", "llm", "embedding"],
                        "default": "deterministic",
                        "description": "Used by operation=rebuild_key_documents. Only 'deterministic'/'auto' are implemented; 'llm'/'embedding' are reserved for future P4-C tiers.",
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="memory_enhance",
            description=_BASE_DESCRIPTIONS["memory_enhance"],
            inputSchema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "classify_record",
                            "extract_candidates",
                            "merge_candidates",
                            "generate_skill_candidate",
                            "explain_conflict",
                            "generate_handoff",
                        ],
                        "description": "Enhancement operation to perform.",
                    },
                    "content_markdown": {"type": "string"},
                    "content": {"type": "string"},
                    "source_record_id": {"type": "string"},
                    "allowed_kinds": {"type": "array", "items": {"type": "string"}},
                    "allowed_scopes": {"type": "array", "items": {"type": "string"}},
                    "allowed_tags": {"type": "array", "items": {"type": "string"}},
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content_markdown": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "additionalProperties": True,
                        },
                    },
                    "records": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content_markdown": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "additionalProperties": True,
                        },
                    },
                    "record_a": {"type": "object", "additionalProperties": True},
                    "record_b": {"type": "object", "additionalProperties": True},
                    "task_id": {"type": "string"},
                    "branch": {"type": "string"},
                    "max_tokens": {"type": "integer", "minimum": 1},
                    "max_chars_per_record": {"type": "integer", "minimum": 256, "default": 4000},
                    "thinking": {"type": "boolean"},
                    "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        ),
    ]


def _build_legacy_tools(file_roles: str, path_hint: str) -> list[Tool]:
    """Legacy/admin tool set (only registered when expose_admin_tools=True)."""
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


def _build_tools(config: MemoryConfig) -> list[Tool]:
    """Build tool definitions with dynamic descriptions from config."""
    file_roles = _build_file_roles(config)

    target_paths = [t.path for t in config.guard_targets]
    path_hint = ", ".join(target_paths) if target_paths else "memory-bank/*.md, .ai-context/*.md"
    facade_tools = _build_facade_tools(file_roles, path_hint)
    if not config.mcp_expose_admin_tools:
        return facade_tools

    legacy_tools = _build_legacy_tools(file_roles, path_hint)
    # Drop legacy `memory_write` because the facade `memory_write` covers it.
    return facade_tools + [tool for tool in legacy_tools if tool.name not in {"memory_write"}]


__all__ = ["_build_file_roles", "_build_facade_tools", "_build_legacy_tools", "_build_tools"]
