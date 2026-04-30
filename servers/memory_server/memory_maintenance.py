from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig
from .memory_events import append_event, count_recent_events
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

    # P2-1: scale-baseline regression check (best-effort).
    extras: dict[str, Any] = {}
    try:
        from .memory_baseline import detect_regressions

        regression_report = detect_regressions(config)
        if regression_report.get("regressions"):
            extras["regressions"] = regression_report["regressions"]
            for r in regression_report["regressions"]:
                issues.append(
                    {
                        "code": "scale_regression",
                        "path": ".ai-memory/baseline.json",
                        "message": (
                            f"metric {r['metric']} grew {r['ratio']}x vs baseline "
                            f"(threshold={r['factor_threshold']}x)"
                        ),
                        **r,
                    }
                )
    except Exception:  # pragma: no cover
        pass

    # P2-2: startup self-heal — clean stale .tmp orphans and lock sidecars.
    extras.update(_self_heal(config))

    # \u00a715.1-D: surface how often the optional vector tier is being skipped
    # in the past 24h so operators can spot index/provider regressions.
    try:
        extras["vector_skip_count_24h"] = count_recent_events(
            config, "vector_supplement_skipped"
        )
    except Exception:  # pragma: no cover - defensive
        pass

    # P2-3: scoring strategy drift warning.
    try:
        from .memory_strategy_hash import detect_strategy_drift

        drift_report = detect_strategy_drift(config)
        if drift_report.get("drift"):
            issues.append(
                {
                    "code": "scoring_strategy_changed",
                    "path": ".ai-memory/events.jsonl",
                    "message": (
                        f"scoring strategy hash changed: "
                        f"previous={drift_report['previous']} current={drift_report['current']}"
                    ),
                    **{k: drift_report[k] for k in ("current", "previous")},
                }
            )
        extras["scoring_strategy_hash"] = drift_report.get("current")
    except Exception:  # pragma: no cover
        pass

    status = "ok" if not issues else "warn"
    return ok_result(
        "health check completed",
        status=status,
        issues=issues,
        stats={"issues": len(issues)},
        **extras,
    )


def _self_heal(config: MemoryConfig) -> dict[str, Any]:
    """P2-2: best-effort cleanup of orphan .tmp files and stale lock sidecars.

    Only acts on files older than a small grace period to avoid racing
    with in-flight writes. Returns ``{"self_heal": {...}}``.
    """
    import time

    grace_seconds = 60
    now = time.time()
    cleaned_tmp: list[str] = []
    cleaned_locks: list[str] = []

    def _maybe_cleanup(root: Path, suffixes: tuple[str, ...], bucket: list[str]) -> None:
        if not root.exists():
            return
        for child in root.rglob("*"):
            try:
                if not child.is_file():
                    continue
                if not child.name.endswith(suffixes):
                    continue
                if (now - child.stat().st_mtime) < grace_seconds:
                    continue
                child.unlink()
                bucket.append(str(child.relative_to(config.repo_root)))
            except OSError:
                continue

    _maybe_cleanup(config.repo_root / "memory-bank", (".tmp",), cleaned_tmp)
    _maybe_cleanup(config.repo_root / ".ai-memory", (".tmp",), cleaned_tmp)
    _maybe_cleanup(config.repo_root / ".ai-memory" / "locks", (".lock",), cleaned_locks)

    return {
        "self_heal": {
            "tmp_removed": cleaned_tmp,
            "stale_locks_removed": cleaned_locks,
        }
    }


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
