from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig
from .memory_paths import PathManager, PathSecurityError
from .memory_records import parse_record_markdown
from .memory_result import error_result, ok_result

FACET_FIELDS = [
    "derived_from_record_ids",
    "derived_from_snapshot_ids",
    "derived_from_revision_ids",
    "supersedes",
    "conflicts_with",
    "related_artifact_ids",
    "asset_paths",
    "map_names",
    "plugin_names",
    "module_names",
    "class_names",
    "blueprint_paths",
]


def _db_path(config: MemoryConfig) -> Path:
    return (config.repo_root / ".ai-memory" / "search.db").resolve()


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]+|[\u3400-\u4dbf\u4e00-\u9fff]+")


def _cjk_ngrams(text: str) -> list[str]:
    grams: list[str] = []
    length = len(text)
    for size in (2, 3):
        if length < size:
            continue
        grams.extend(text[index : index + size] for index in range(0, length - size + 1))
    if not grams and text:
        grams.append(text)
    return grams


def build_search_text(
    *,
    title: str,
    body: str,
    tags: list[str],
    metadata_values: list[str],
) -> str:
    """Build dependency-free FTS text with CJK n-grams and structured metadata."""
    parts = [title, body, " ".join(tags), " ".join(metadata_values)]
    tokens: list[str] = []

    for part in parts:
        for token in _TOKEN_RE.findall(part):
            if re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]+", token):
                tokens.extend(_cjk_ngrams(token))
            else:
                tokens.append(token.lower())

    return " ".join(tokens)


def _connect(config: MemoryConfig) -> sqlite3.Connection:
    path = _db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def _is_index_healthy(config: MemoryConfig) -> bool:
    """Cheap integrity probe so callers can recover from a corrupted db file."""
    path = _db_path(config)
    if not path.exists():
        return True  # nothing to be unhealthy
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and str(row[0]).lower() == "ok"
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def _reset_corrupted_index(config: MemoryConfig) -> None:
    """Remove a corrupted index file so the next ``_connect`` builds fresh.

    Windows keeps file handles alive briefly after a connection is GC'd; we
    nudge GC and retry once before giving up so callers don't see a spurious
    ``PermissionError``.
    """
    import gc

    path = _db_path(config)
    gc.collect()
    for attempt in range(2):
        try:
            path.unlink()
            break
        except FileNotFoundError:
            break
        except PermissionError:
            if attempt == 0:
                gc.collect()
                continue
            return
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
        except PermissionError:
            pass


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_records (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            schema_version TEXT,
            record_kind TEXT NOT NULL,
            scope TEXT NOT NULL,
            status TEXT NOT NULL,
            author TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            task_id TEXT,
            branch TEXT,
            occurred_at TEXT,
            valid_from TEXT,
            valid_to TEXT,
            memory_tier TEXT,
            cognitive_level TEXT,
            importance_score REAL,
            system_area TEXT,
            facets_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT,
            updated_at TEXT,
            title TEXT NOT NULL,
            body TEXT NOT NULL
        )
        """
    )
    existing_record_columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_records)").fetchall()}
    column_specs = {
        "schema_version": "TEXT",
        "occurred_at": "TEXT",
        "valid_from": "TEXT",
        "valid_to": "TEXT",
        "memory_tier": "TEXT",
        "cognitive_level": "TEXT",
        "importance_score": "REAL",
        "system_area": "TEXT",
        "facets_json": "TEXT NOT NULL DEFAULT '[]'",
    }
    for column, spec in column_specs.items():
        if column not in existing_record_columns:
            conn.execute(f"ALTER TABLE memory_records ADD COLUMN {column} {spec}")
    existing_fts_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(memory_records_fts)").fetchall()
    }
    if existing_fts_columns and "search_text" not in existing_fts_columns:
        conn.execute("DROP TABLE memory_records_fts")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_records_fts USING fts5(
            id UNINDEXED,
            path UNINDEXED,
            title,
            body,
            tags,
            search_text
        )
        """
    )


def _metadata_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _facet_values(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in FACET_FIELDS:
        values.extend(_metadata_list(metadata, key))
    return values


def _metadata_search_values(metadata: dict[str, Any]) -> list[str]:
    values = [
        str(metadata.get("schema_version", "") or ""),
        str(metadata.get("record_kind", "") or ""),
        str(metadata.get("scope", "") or ""),
        str(metadata.get("status", "") or ""),
        str(metadata.get("author", "") or ""),
        str(metadata.get("task_id", "") or ""),
        str(metadata.get("branch", "") or ""),
        str(metadata.get("memory_tier", "") or ""),
        str(metadata.get("cognitive_level", "") or ""),
        str(metadata.get("system_area", "") or ""),
    ]
    values.extend(_facet_values(metadata))
    return values


def _iter_record_files(config: MemoryConfig) -> tuple[list[tuple[str, dict[str, Any], str]], dict[str, int]]:
    manager = PathManager(config)
    records: list[tuple[str, dict[str, Any], str]] = []
    stats = {
        "scanned_files": 0,
        "skipped_non_records": 0,
        "skipped_read_errors": 0,
    }

    for abs_path, rel_path in manager.iter_files(scopes=["memory-bank"], include_paths=["memory-bank/**/*.md"]):
        stats["scanned_files"] += 1
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            metadata, body = parse_record_markdown(text)
        except (OSError, ValueError):
            stats["skipped_non_records"] += 1
            continue
        if not metadata.get("id") or not metadata.get("record_kind"):
            stats["skipped_non_records"] += 1
            continue
        records.append((rel_path, metadata, body))

    return records, stats


def memory_rebuild_index(config: MemoryConfig) -> dict[str, Any]:
    """Rebuild the SQLite FTS index from record Markdown files."""
    try:
        records, stats = _iter_record_files(config)
    except PathSecurityError as exc:
        return error_result("path_not_allowed", str(exc))
    except FileNotFoundError as exc:
        return error_result("not_found", str(exc))

    # If a previous crash / disk error left a corrupted db file, drop it so
    # the rebuild can start from a clean schema instead of failing on every
    # subsequent call.
    if not _is_index_healthy(config):
        _reset_corrupted_index(config)

    try:
        with _connect(config) as conn:
            _ensure_schema(conn)
            conn.execute("DELETE FROM memory_records")
            conn.execute("DELETE FROM memory_records_fts")
            for rel_path, metadata, body in records:
                tags = [str(tag) for tag in metadata.get("tags", []) if str(tag)]
                title = _first_heading(body)
                facets = _facet_values(metadata)
                metadata_values = _metadata_search_values(metadata)
                search_text = build_search_text(
                    title=title,
                    body=body,
                    tags=tags,
                    metadata_values=metadata_values,
                )
                conn.execute(
                    """
                    INSERT INTO memory_records (
                        id, path, schema_version, record_kind, scope, status, author, tags_json,
                        task_id, branch, occurred_at, valid_from, valid_to, memory_tier,
                        cognitive_level, importance_score, system_area, facets_json,
                        created_at, updated_at, title, body
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(metadata.get("id")),
                        rel_path,
                        metadata.get("schema_version"),
                        str(metadata.get("record_kind", "")),
                        str(metadata.get("scope", "")),
                        str(metadata.get("status", "")),
                        str(metadata.get("author", "")),
                        json.dumps(tags, ensure_ascii=False),
                        metadata.get("task_id"),
                        metadata.get("branch"),
                        metadata.get("occurred_at"),
                        metadata.get("valid_from"),
                        metadata.get("valid_to"),
                        metadata.get("memory_tier"),
                        metadata.get("cognitive_level"),
                        metadata.get("importance_score"),
                        metadata.get("system_area"),
                        json.dumps(facets, ensure_ascii=False),
                        metadata.get("created_at"),
                        metadata.get("updated_at"),
                        title,
                        body,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO memory_records_fts (id, path, title, body, tags, search_text)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(metadata.get("id")),
                        rel_path,
                        title,
                        body,
                        " ".join(tags),
                        search_text,
                    ),
                )
    except sqlite3.Error as exc:
        return error_result("index_failed", f"failed to rebuild index: {exc}")

    return ok_result(
        "index rebuilt",
        indexed_records=len(records),
        db_path=_db_path(config).relative_to(config.repo_root).as_posix(),
        stats=stats,
    )


def _index_record_rows(config: MemoryConfig, rows: list[tuple[str, dict[str, Any], str]]) -> None:
    with _connect(config) as conn:
        _ensure_schema(conn)
        for rel_path, metadata, body in rows:
            tags = [str(tag) for tag in metadata.get("tags", []) if str(tag)]
            title = _first_heading(body)
            facets = _facet_values(metadata)
            metadata_values = _metadata_search_values(metadata)
            search_text = build_search_text(title=title, body=body, tags=tags, metadata_values=metadata_values)
            conn.execute("DELETE FROM memory_records WHERE id = ?", (str(metadata.get("id")),))
            conn.execute("DELETE FROM memory_records_fts WHERE id = ?", (str(metadata.get("id")),))
            conn.execute(
                """
                INSERT INTO memory_records (
                    id, path, schema_version, record_kind, scope, status, author, tags_json,
                    task_id, branch, occurred_at, valid_from, valid_to, memory_tier,
                    cognitive_level, importance_score, system_area, facets_json,
                    created_at, updated_at, title, body
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(metadata.get("id")),
                    rel_path,
                    metadata.get("schema_version"),
                    str(metadata.get("record_kind", "")),
                    str(metadata.get("scope", "")),
                    str(metadata.get("status", "")),
                    str(metadata.get("author", "")),
                    json.dumps(tags, ensure_ascii=False),
                    metadata.get("task_id"),
                    metadata.get("branch"),
                    metadata.get("occurred_at"),
                    metadata.get("valid_from"),
                    metadata.get("valid_to"),
                    metadata.get("memory_tier"),
                    metadata.get("cognitive_level"),
                    metadata.get("importance_score"),
                    metadata.get("system_area"),
                    json.dumps(facets, ensure_ascii=False),
                    metadata.get("created_at"),
                    metadata.get("updated_at"),
                    title,
                    body,
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_records_fts (id, path, title, body, tags, search_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(metadata.get("id")), rel_path, title, body, " ".join(tags), search_text),
            )


def memory_update_index(config: MemoryConfig, *, paths: list[str]) -> dict[str, Any]:
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths) or not paths:
        return error_result("invalid_input", "paths must be a non-empty list of strings")
    manager = PathManager(config)
    rows: list[tuple[str, dict[str, Any], str]] = []
    skipped = 0
    try:
        for path in paths:
            abs_path = manager.resolve(path, must_exist=True, must_be_file=True)
            rel_path = manager.to_repo_relative(abs_path)
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            metadata, body = parse_record_markdown(text)
            if not metadata.get("id") or not metadata.get("record_kind"):
                skipped += 1
                continue
            rows.append((rel_path, metadata, body))
        _index_record_rows(config, rows)
    except PathSecurityError as exc:
        return error_result("path_not_allowed", str(exc))
    except FileNotFoundError as exc:
        return error_result("not_found", str(exc))
    except (OSError, ValueError, sqlite3.Error) as exc:
        return error_result("index_failed", f"failed to update index: {exc}")

    return ok_result(
        "index updated",
        indexed_records=len(rows),
        skipped_records=skipped,
        db_path=_db_path(config).relative_to(config.repo_root).as_posix(),
    )


def _escape_fts5_token(token: str) -> str:
    """Wrap a token in double quotes for FTS5 MATCH, escaping internal quotes.

    FTS5 treats wide ranges of characters as syntax (`-`, `:`, `*`, `(`, `)`, etc.)
    plus the bareword keywords AND/OR/NOT/NEAR. Quoting every token as a phrase
    eliminates that surface entirely and is safe for both ASCII and CJK content.
    """
    return '"' + token.replace('"', '""') + '"'


def build_fts5_match_query(query: str) -> str:
    """Build a safe FTS5 MATCH expression from a free-form user query.

    The query is normalized through the same tokenizer used at index time
    (Latin/digit tokens lower-cased, CJK runs expanded into bigrams/trigrams)
    and every token is wrapped as a phrase before being joined by spaces (AND).
    Empty queries return an empty string so callers can short-circuit.
    """
    search_text = build_search_text(title=query, body="", tags=[], metadata_values=[])
    tokens = [token for token in search_text.split() if token]
    if not tokens:
        return ""
    return " ".join(_escape_fts5_token(token) for token in tokens)


def _query_index(conn: sqlite3.Connection, query: str, top_k: int) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    match_expr = build_fts5_match_query(query)
    if not match_expr:
        return []
    return conn.execute(
        """
        SELECT
            r.id,
            r.path,
            r.schema_version,
            r.record_kind,
            r.scope,
            r.status,
            r.author,
            r.tags_json,
            r.task_id,
            r.branch,
            r.occurred_at,
            r.valid_from,
            r.valid_to,
            r.memory_tier,
            r.cognitive_level,
            r.importance_score,
            r.system_area,
            r.facets_json,
            r.title,
            snippet(memory_records_fts, 3, '[', ']', ' ... ', 12) AS snippet,
            bm25(memory_records_fts) AS rank
        FROM memory_records_fts
        JOIN memory_records AS r ON r.id = memory_records_fts.id
        WHERE memory_records_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (match_expr, top_k),
    ).fetchall()


def memory_search_records(config: MemoryConfig, query: str, *, top_k: int | None = None) -> dict[str, Any]:
    query_normalized = query.strip()
    if not query_normalized:
        return error_result("invalid_input", "query must not be empty")
    if top_k is None:
        top_k = 10
    if top_k <= 0:
        return error_result("invalid_input", "top_k must be > 0")

    db_file = _db_path(config)
    if not db_file.exists():
        rebuild = memory_rebuild_index(config)
        if not rebuild.get("ok"):
            return rebuild

    try:
        with _connect(config) as conn:
            _ensure_schema(conn)
            rows = _query_index(conn, query_normalized, top_k)
    except sqlite3.Error as exc:
        return error_result("search_failed", f"failed to search record index: {exc}")

    results: list[dict[str, Any]] = []
    for row in rows:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
        facets = json.loads(row["facets_json"]) if row["facets_json"] else []
        results.append(
            {
                "id": row["id"],
                "path": row["path"],
                "schema_version": row["schema_version"],
                "record_kind": row["record_kind"],
                "scope": row["scope"],
                "status": row["status"],
                "author": row["author"],
                "tags": tags,
                "task_id": row["task_id"],
                "branch": row["branch"],
                "occurred_at": row["occurred_at"],
                "valid_from": row["valid_from"],
                "valid_to": row["valid_to"],
                "memory_tier": row["memory_tier"],
                "cognitive_level": row["cognitive_level"],
                "importance_score": row["importance_score"],
                "system_area": row["system_area"],
                "facets": facets,
                "title": row["title"],
                "snippet": row["snippet"],
                "score": float(-row["rank"]),
            }
        )

    return ok_result(
        "record search completed",
        query=query,
        results=results,
        stats={
            "total_hits": len(results),
            "returned_hits": len(results),
            "db_path": db_file.relative_to(config.repo_root).as_posix(),
        },
    )
