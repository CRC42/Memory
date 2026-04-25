from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig
from .memory_events import append_event
from .memory_locks import file_lock
from .memory_paths import PathSecurityError
from .memory_record_io import (
    find_record_by_id as _find_record,
    iter_record_files as _iter_record_files,
)
from .memory_records import parse_record_markdown, render_record_markdown
from .memory_result import error_result, ok_result


REQUIRED_METADATA = {"schema_version", "id", "record_kind", "scope", "status", "author"}


def memory_health_check(config: MemoryConfig) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    try:
        files = _iter_record_files(config)
    except (PathSecurityError, FileNotFoundError) as exc:
        return error_result("path_error", str(exc))

    for abs_path, rel_path in files:
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            metadata, _body = parse_record_markdown(text)
        except ValueError as exc:
            try:
                looks_like_record = abs_path.read_text(encoding="utf-8", errors="replace").startswith("---\n")
            except OSError:
                looks_like_record = False
            if looks_like_record:
                issues.append({"code": "invalid_record_format", "path": rel_path, "message": str(exc)})
            continue
        except OSError as exc:
            issues.append({"code": "read_failed", "path": rel_path, "message": str(exc)})
            continue
        missing = sorted(key for key in REQUIRED_METADATA if not metadata.get(key))
        if missing:
            issues.append(
                {
                    "code": "missing_required_metadata",
                    "path": rel_path,
                    "message": f"missing required metadata: {', '.join(missing)}",
                    "missing": missing,
                }
            )
        tags = metadata.get("tags")
        if isinstance(tags, list) and config.tag_allowed_tags:
            unknown = sorted(set(str(tag) for tag in tags) - set(config.tag_allowed_tags))
            if unknown:
                issues.append(
                    {
                        "code": "unknown_tags",
                        "path": rel_path,
                        "message": f"unknown tags: {', '.join(unknown)}",
                        "tags": unknown,
                    }
                )

    search_db = config.repo_root / ".ai-memory" / "search.db"
    if not search_db.exists():
        issues.append({"code": "missing_search_db", "path": ".ai-memory/search.db", "message": "search index missing"})

    status = "ok" if not issues else "warn"
    return ok_result("health check completed", status=status, issues=issues, stats={"issues": len(issues)})


def memory_migrate_records(config: MemoryConfig, *, target_schema_version: str = "1.0") -> dict[str, Any]:
    try:
        files = _iter_record_files(config)
    except (PathSecurityError, FileNotFoundError) as exc:
        return error_result("path_error", str(exc))

    migrated: list[str] = []
    for abs_path, rel_path in files:
        try:
            metadata, body = parse_record_markdown(abs_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        current = str(metadata.get("schema_version", ""))
        if current == target_schema_version:
            continue
        metadata["schema_migrated_from"] = current
        metadata["schema_version"] = target_schema_version
        metadata["schema_migrated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            abs_path.write_text(render_record_markdown(metadata, body), encoding="utf-8")
            migrated.append(rel_path)
        except OSError:
            continue

    append_event(config, "memory_migrate_records", {"target_schema_version": target_schema_version, "paths": migrated})
    return ok_result("records migrated", migrated_records=len(migrated), paths=migrated)


def memory_delete_record(config: MemoryConfig, record_id: str, *, reason: str | None = None) -> dict[str, Any]:
    found = _find_record(config, record_id)
    if isinstance(found, dict):
        return found
    abs_path, rel_path, metadata, _body = found
    if metadata.get("status") != "archived":
        return error_result("invalid_state", "only archived records can be deleted", record_id=record_id)

    tombstone = {
        "deleted_at": datetime.now(timezone.utc).isoformat(),
        "id": record_id,
        "path": rel_path,
        "reason": reason,
    }
    try:
        abs_path.unlink()
        tombstone_path = config.repo_root / ".ai-memory" / "tombstones.jsonl"
        tombstone_path.parent.mkdir(parents=True, exist_ok=True)
        # Cross-process serialization: tombstones is the audit trail for
        # deletions; concurrent appends from multiple MCP server
        # processes must not produce torn lines.
        with file_lock(config.repo_root, tombstone_path):
            with tombstone_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(tombstone, ensure_ascii=False) + "\n")
                handle.flush()
                # Durability: ensure tombstone reaches disk under
                # mcp.fsync_strict; best-effort otherwise.
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    if config.mcp_fsync_strict:
                        raise
    except OSError as exc:
        return error_result("delete_failed", f"failed to delete record: {exc}")

    append_event(config, "memory_delete_record", tombstone)
    return ok_result("record deleted", id=record_id, path=rel_path, tombstone=tombstone)
