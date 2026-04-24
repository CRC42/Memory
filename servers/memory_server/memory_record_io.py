"""Shared low-level record I/O helpers.

This module exists so that governance, lineage, maintenance, compiler and
retrieval no longer each carry their own copies of ``_iter_records``,
``_find_record``, ``_refresh_index_if_exists`` and ``_write_record_to_target``.

Only deterministic file-system primitives live here. Callers continue to wrap
these helpers with their own domain-specific logic (e.g. ``CompilableRecord``
projections in ``memory_compiler``).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig
from .memory_paths import PathManager, PathSecurityError
from .memory_records import parse_record_markdown, render_record_markdown, target_path_for_record
from .memory_result import error_result, ok_result


def _atomic_write_text(target: Path, content: str) -> None:
    """Atomically write ``content`` to ``target`` (UTF-8).

    Hardening (P1-F/G):
    - Tmp file is created in the *same directory* as ``target`` so
      ``os.replace`` stays on one volume.
    - Tmp file is created with ``O_CREAT | O_EXCL`` to refuse to clobber a
      stale tmp from another writer in the same tick.
    - The tmp file's contents are flushed and ``fsync``-ed before rename so a
      crash between rename and writeback cannot leave a half-empty file.
    - On any failure the tmp file is removed.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.parent / f".{target.name}.{uuid.uuid4().hex[:8]}.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY  # Windows: avoid CR/LF translation
    fd = os.open(str(tmp_path), flags, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content.encode("utf-8"))
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # Some filesystems / mounts do not support fsync; durability
                # is best-effort, but we must still complete the rename.
                pass
        os.replace(tmp_path, target)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class ParsedRecord:
    """A successfully parsed Markdown record on disk."""

    abs_path: Path
    rel_path: str
    metadata: dict[str, Any]
    body: str


def iter_record_files(config: MemoryConfig) -> list[tuple[Path, str]]:
    """Return all ``memory-bank/`` markdown files except compiled views."""
    manager = PathManager(config)
    return [
        (abs_path, rel_path)
        for abs_path, rel_path in manager.iter_files(
            scopes=["memory-bank"], include_paths=["memory-bank/**/*.md"]
        )
        if not rel_path.startswith("memory-bank/compiled/")
    ]


def iter_parsed_records(config: MemoryConfig) -> tuple[list[ParsedRecord], dict[str, int]]:
    """Iterate every record file and parse Markdown + Front Matter.

    Returns a list of ``ParsedRecord`` and a stats dict with keys
    ``scanned_files``, ``skipped_non_records`` and ``skipped_read_errors``.
    Files without ``id`` or ``record_kind`` metadata are skipped.
    """
    records: list[ParsedRecord] = []
    stats = {
        "scanned_files": 0,
        "skipped_non_records": 0,
        "skipped_read_errors": 0,
    }
    for abs_path, rel_path in iter_record_files(config):
        stats["scanned_files"] += 1
        try:
            metadata, body = parse_record_markdown(
                abs_path.read_text(encoding="utf-8", errors="replace")
            )
        except ValueError:
            stats["skipped_non_records"] += 1
            continue
        except OSError:
            stats["skipped_read_errors"] += 1
            continue
        if not metadata.get("id") or not metadata.get("record_kind"):
            stats["skipped_non_records"] += 1
            continue
        records.append(ParsedRecord(abs_path=abs_path, rel_path=rel_path, metadata=metadata, body=body))
    return records, stats


def find_record_by_id(
    config: MemoryConfig, record_id: str
) -> tuple[Path, str, dict[str, Any], str] | dict[str, Any]:
    """Find a record by id. Returns the 4-tuple, or an ``error_result`` dict."""
    try:
        records, _stats = iter_parsed_records(config)
    except PathSecurityError as exc:
        return error_result("path_not_allowed", str(exc))
    except FileNotFoundError as exc:
        return error_result("not_found", str(exc))
    for record in records:
        if str(record.metadata.get("id")) == record_id:
            return record.abs_path, record.rel_path, record.metadata, record.body
    return error_result("not_found", f"record not found: {record_id}", record_id=record_id)


def refresh_index_if_exists(config: MemoryConfig, path: str) -> None:
    """Update the SQLite FTS index for ``path`` if the index already exists.

    Best-effort: any failure is swallowed because the index is a derived view
    and callers must not fail their primary write because of a stale index.
    """
    if not (config.repo_root / ".ai-memory/search.db").exists():
        return
    try:
        from .memory_record_index import memory_update_index

        memory_update_index(config, paths=[path])
    except Exception:
        pass


def write_same_record(
    config: MemoryConfig,
    *,
    abs_path: Path,
    rel_path: str,
    metadata: dict[str, Any],
    body: str,
) -> dict[str, Any]:
    """Re-render and atomically replace an existing record at the same path."""
    content = render_record_markdown(metadata, body)
    try:
        _atomic_write_text(abs_path, content)
    except OSError as exc:
        return error_result("write_failed", f"failed to update record: {exc}")

    refresh_index_if_exists(config, rel_path)
    return ok_result(
        "record updated",
        id=metadata.get("id"),
        path=rel_path,
        record_kind=metadata.get("record_kind"),
        scope=metadata.get("scope"),
        status=metadata.get("status"),
    )


def write_record_to_target(
    config: MemoryConfig,
    *,
    old_abs_path: Path,
    old_rel_path: str,
    metadata: dict[str, Any],
    body: str,
) -> dict[str, Any]:
    """Atomically write the record to its canonical target path.

    Used by status transitions (validate/publish/archive). The new file is
    staged in a sibling temp file then atomically renamed; only after that
    succeeds is the previous file removed. This avoids "two copies" or
    "zero copies" outcomes on partial failure.

    Hardening (P1-F): if the resolved target path differs from the source
    path AND a file already exists at the target, we refuse to overwrite —
    that situation indicates either a record-id collision or a stale
    governance file from a partial earlier write, both of which deserve a
    surfaced error rather than silent clobber.
    """
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

    same_path = old_abs_path.resolve() == new_abs_path.resolve()
    if not same_path and new_abs_path.exists():
        return error_result(
            "target_exists",
            f"refusing to overwrite existing record at target: {rel_path}",
            path=rel_path,
            previous_path=old_rel_path,
        )

    content = render_record_markdown(metadata, body)
    try:
        _atomic_write_text(new_abs_path, content)
        if not same_path:
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
