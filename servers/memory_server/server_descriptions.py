"""Static base descriptions and server constants for the memory MCP server.

Extracted from `server.py` (P1-A). Pure data; no runtime side effects.
"""

from __future__ import annotations

SERVER_NAME = "generic-memory-mcp"
SERVER_VERSION = "0.5.3"

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
        "important memory output, and P3 context retrieval through an operation field."
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


__all__ = ["SERVER_NAME", "SERVER_VERSION", "_BASE_DESCRIPTIONS"]
