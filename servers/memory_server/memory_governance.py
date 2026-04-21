from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig
from .memory_events import append_event, get_current_user
from .memory_paths import PathManager, PathSecurityError
from .memory_records import parse_record_markdown, render_record_markdown, target_path_for_record
from .memory_result import error_result, ok_result


def _iter_record_paths(config: MemoryConfig) -> list[tuple[Path, str]]:
    manager = PathManager(config)
    return list(manager.iter_files(scopes=["memory-bank"], include_paths=["memory-bank/**/*.md"]))


def _find_record(config: MemoryConfig, record_id: str) -> tuple[Path, str, dict[str, Any], str] | dict[str, Any]:
    try:
        files = _iter_record_paths(config)
    except PathSecurityError as exc:
        return error_result("path_not_allowed", str(exc))
    except FileNotFoundError as exc:
        return error_result("not_found", str(exc))

    for abs_path, rel_path in files:
        if rel_path.startswith("memory-bank/compiled/"):
            continue
        try:
            metadata, body = parse_record_markdown(abs_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if str(metadata.get("id")) == record_id:
            return abs_path, rel_path, metadata, body
    return error_result("not_found", f"record not found: {record_id}", record_id=record_id)


def _write_record_to_target(
    config: MemoryConfig,
    *,
    old_abs_path: Path,
    old_rel_path: str,
    metadata: dict[str, Any],
    body: str,
) -> dict[str, Any]:
    record_id = str(metadata.get("id", ""))
    rel_path = target_path_for_record(
        record_id,
        str(metadata.get("record_kind", "")),
        str(metadata.get("scope", "")),
        str(metadata.get("status", "")),
        str(metadata.get("author", "")),
    )
    manager = PathManager(config)
    try:
        new_abs_path = manager.resolve(rel_path, must_exist=False, must_be_file=False)
    except PathSecurityError as exc:
        return error_result("path_not_allowed", str(exc))

    content = render_record_markdown(metadata, body)
    same_path = old_abs_path.resolve() == new_abs_path.resolve()
    try:
        new_abs_path.parent.mkdir(parents=True, exist_ok=True)
        # Stage the new content in a sibling temp file, then atomically rename.
        # This avoids the previous "write new -> unlink old" path that could leave
        # two copies (or zero) on partial failure during status transitions.
        tmp_name = f".{new_abs_path.name}.{uuid.uuid4().hex[:8]}.tmp"
        tmp_path = new_abs_path.parent / tmp_name
        tmp_path.write_text(content, encoding="utf-8")
        try:
            os.replace(tmp_path, new_abs_path)
        except OSError:
            # Best-effort cleanup of staging file before re-raising.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise
        if not same_path:
            # Only remove the previous file once the new one is in place.
            try:
                old_abs_path.unlink()
            except FileNotFoundError:
                pass
    except OSError as exc:
        return error_result("write_failed", f"failed to update record: {exc}")

    return ok_result(
        "record updated",
        id=record_id,
        path=rel_path,
        previous_path=old_rel_path,
        status=metadata.get("status"),
        scope=metadata.get("scope"),
        record_kind=metadata.get("record_kind"),
    )


def _refresh_index_if_exists(config: MemoryConfig, path: str) -> None:
    if not (config.repo_root / ".ai-memory/search.db").exists():
        return
    try:
        from .memory_record_index import memory_update_index

        memory_update_index(config, paths=[path])
    except Exception:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip().lower()
    return ""


def _display_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _body_fingerprint(body: str) -> str:
    return " ".join(body.lower().split())


def _other_records(config: MemoryConfig, record_id: str) -> list[tuple[str, dict[str, Any], str]]:
    records: list[tuple[str, dict[str, Any], str]] = []
    for abs_path, rel_path in _iter_record_paths(config):
        if rel_path.startswith("memory-bank/compiled/"):
            continue
        try:
            metadata, body = parse_record_markdown(abs_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if str(metadata.get("id")) != record_id:
            records.append((rel_path, metadata, body))
    return records


def _validation_errors(config: MemoryConfig, record_id: str, metadata: dict[str, Any], body: str) -> list[str]:
    errors: list[str] = []
    record_kind = str(metadata.get("record_kind", ""))
    source_refs = metadata.get("source_refs") if isinstance(metadata.get("source_refs"), list) else []
    confidence = metadata.get("confidence")

    if record_kind in (config.governance_require_source_refs_for or []) and not source_refs:
        errors.append("source_refs are required for this candidate type")
    if isinstance(confidence, (int, float)) and confidence < config.governance_min_confidence:
        errors.append(f"confidence {confidence} is below minimum {config.governance_min_confidence}")

    title = _first_heading(body)
    fingerprint = _body_fingerprint(body)
    for _path, other_metadata, other_body in _other_records(config, record_id):
        if other_metadata.get("status") not in {"validated", "published"}:
            continue
        if _first_heading(other_body) == title and _body_fingerprint(other_body) == fingerprint:
            errors.append(f"duplicate record title/body: {title}")
            break
    return errors


def _publish_conflict(config: MemoryConfig, record_id: str, body: str) -> str | None:
    title = _first_heading(body)
    fingerprint = _body_fingerprint(body)
    for _path, other_metadata, other_body in _other_records(config, record_id):
        if other_metadata.get("status") != "published" or other_metadata.get("scope") != "shared":
            continue
        if _first_heading(other_body) == title and _body_fingerprint(other_body) != fingerprint:
            return _display_heading(other_body) or title
    return None


def memory_validate_candidate(
    config: MemoryConfig,
    record_id: str,
    *,
    validated_by: str | None = None,
) -> dict[str, Any]:
    found = _find_record(config, record_id)
    if isinstance(found, dict):
        return found
    old_abs_path, old_rel_path, metadata, body = found

    if str(metadata.get("status")) not in {"candidate", "raw"}:
        return error_result("invalid_state", "record must be candidate or raw before validation", record_id=record_id)
    if not str(metadata.get("record_kind", "")).endswith("_candidate"):
        return error_result("invalid_state", "record_kind must be a candidate type before validation", record_id=record_id)
    if config.governance_reviewers and validated_by not in config.governance_reviewers:
        return error_result("permission_denied", "validated_by must be one of configured reviewers", record_id=record_id)

    validation_errors = _validation_errors(config, record_id, metadata, body)
    if validation_errors:
        return error_result(
            "validation_failed",
            "candidate failed validation rules",
            record_id=record_id,
            validation_errors=validation_errors,
        )

    metadata["status"] = "validated"
    metadata["validated_by"] = validated_by or get_current_user(config.repo_root)
    metadata["updated_at"] = _now()
    if metadata.get("scope") in {None, "local"}:
        metadata["scope"] = "personal"

    result = _write_record_to_target(
        config,
        old_abs_path=old_abs_path,
        old_rel_path=old_rel_path,
        metadata=metadata,
        body=body,
    )
    if result.get("ok"):
        _refresh_index_if_exists(config, result["path"])
        append_event(
            config,
            "memory_validate_candidate",
            {
                "id": record_id,
                "previous_path": old_rel_path,
                "path": result["path"],
                "validated_by": metadata.get("validated_by"),
            },
        )
    return result


def memory_publish_candidate(
    config: MemoryConfig,
    record_id: str,
    *,
    published_by: str | None = None,
) -> dict[str, Any]:
    found = _find_record(config, record_id)
    if isinstance(found, dict):
        return found
    old_abs_path, old_rel_path, metadata, body = found

    if metadata.get("status") != "validated":
        return error_result("invalid_state", "record must be validated before publishing", record_id=record_id)
    if not metadata.get("validated_by"):
        return error_result("invalid_state", "record must have validated_by before publishing", record_id=record_id)
    if config.governance_publish_owners and published_by not in config.governance_publish_owners:
        return error_result("permission_denied", "published_by must be one of configured publish owners", record_id=record_id)
    conflict_title = _publish_conflict(config, record_id, body)
    if conflict_title:
        return error_result("conflict_detected", f"candidate conflicts with published system rule: {conflict_title}", record_id=record_id)

    metadata["status"] = "published"
    metadata["scope"] = "shared"
    if str(metadata.get("record_kind", "")).endswith("_candidate"):
        metadata["record_kind"] = "system_rule"
    metadata["published_by"] = published_by or get_current_user(config.repo_root)
    metadata["published_at"] = _now()
    metadata["updated_at"] = metadata["published_at"]

    result = _write_record_to_target(
        config,
        old_abs_path=old_abs_path,
        old_rel_path=old_rel_path,
        metadata=metadata,
        body=body,
    )
    if result.get("ok"):
        _refresh_index_if_exists(config, result["path"])
        append_event(
            config,
            "memory_publish_candidate",
            {
                "id": record_id,
                "previous_path": old_rel_path,
                "path": result["path"],
                "published_by": metadata.get("published_by"),
            },
        )
    return result


def memory_archive_record(
    config: MemoryConfig,
    record_id: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    found = _find_record(config, record_id)
    if isinstance(found, dict):
        return found
    old_abs_path, old_rel_path, metadata, body = found

    metadata["status"] = "archived"
    metadata["scope"] = "archive"
    metadata["archive_reason"] = reason
    metadata["archived_at"] = _now()
    metadata["updated_at"] = metadata["archived_at"]

    result = _write_record_to_target(
        config,
        old_abs_path=old_abs_path,
        old_rel_path=old_rel_path,
        metadata=metadata,
        body=body,
    )
    if result.get("ok"):
        _refresh_index_if_exists(config, result["path"])
        append_event(
            config,
            "memory_archive_record",
            {
                "id": record_id,
                "previous_path": old_rel_path,
                "path": result["path"],
                "reason": reason,
            },
        )
    return result
