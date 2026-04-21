from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig
from .memory_events import append_event, get_current_user
from .memory_paths import PathManager, PathSecurityError
from .memory_records import parse_record_markdown, render_record_markdown
from .memory_result import error_result, ok_result

SUPPORTED_TARGETS = {"runtime_digest", "task_handoff", "system_digest", "publish_queue"}
DEFAULT_INCLUDE_SCOPES = ["shared", "personal"]
DEFAULT_INCLUDE_STATUSES = ["validated", "published"]


@dataclass(frozen=True)
class CompilableRecord:
    path: str
    metadata: dict[str, Any]
    body: str
    title: str


def _slug(value: str, *, fallback: str) -> str:
    normalized = value.replace("\\", "/")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", normalized).strip("-._")
    return slug or fallback


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return "Untitled Record"


def _compiled_path(target: str, *, user: str | None = None, task_id: str | None = None, branch: str | None = None) -> str:
    if target == "task_handoff":
        task_slug = _slug(task_id or "handoff", fallback="handoff")
        return f"memory-bank/compiled/runtime/task/{task_slug}-handoff.md"
    if target == "publish_queue":
        return "memory-bank/compiled/publish/publish-queue.md"
    if target == "system_digest":
        return "memory-bank/compiled/runtime/system-digest.md"
    if task_id:
        return f"memory-bank/compiled/runtime/task/{_slug(task_id, fallback='task')}.md"
    if branch:
        return f"memory-bank/compiled/runtime/branch/{_slug(branch, fallback='branch')}.md"
    if user:
        return f"memory-bank/compiled/runtime/people/{_slug(user, fallback='user')}-digest.md"
    return "memory-bank/compiled/runtime/system-digest.md"


def _iter_records(config: MemoryConfig) -> tuple[list[CompilableRecord], dict[str, int]]:
    manager = PathManager(config)
    records: list[CompilableRecord] = []
    stats = {
        "scanned_files": 0,
        "skipped_non_records": 0,
        "skipped_read_errors": 0,
    }

    for abs_path, rel_path in manager.iter_files(scopes=["memory-bank"], include_paths=["memory-bank/**/*.md"]):
        if rel_path.startswith("memory-bank/compiled/"):
            continue
        stats["scanned_files"] += 1
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            metadata, body = parse_record_markdown(text)
        except ValueError:
            stats["skipped_non_records"] += 1
            continue
        except OSError:
            stats["skipped_read_errors"] += 1
            continue
        if not metadata.get("id") or not metadata.get("record_kind"):
            stats["skipped_non_records"] += 1
            continue
        records.append(
            CompilableRecord(
                path=rel_path,
                metadata=metadata,
                body=body.strip(),
                title=_first_heading(body),
            )
        )
    return records, stats


def _matches_filter(
    record: CompilableRecord,
    *,
    user: str | None,
    task_id: str | None,
    branch: str | None,
    include_scopes: list[str],
    include_statuses: list[str],
    preferred_tags: list[str],
) -> bool:
    metadata = record.metadata
    scope = str(metadata.get("scope", ""))
    status = str(metadata.get("status", ""))
    author = str(metadata.get("author", ""))
    record_task_id = metadata.get("task_id")
    record_branch = metadata.get("branch")
    tags = [str(tag) for tag in metadata.get("tags", []) if str(tag)]

    if scope not in include_scopes:
        return False
    if status not in include_statuses:
        return False
    if scope == "personal" and user and author != user:
        return False
    if task_id and record_task_id != task_id:
        return False
    if branch and record_branch not in (None, branch):
        return False
    if preferred_tags and not set(preferred_tags).intersection(tags):
        return False
    return True


def _record_sort_key(record: CompilableRecord) -> tuple[int, str, str]:
    status = str(record.metadata.get("status", ""))
    scope = str(record.metadata.get("scope", ""))
    status_rank = {"published": 0, "validated": 1, "candidate": 2, "raw": 3, "archived": 4}.get(status, 9)
    scope_rank = {"shared": 0, "personal": 1, "archive": 2, "local": 3}.get(scope, 9)
    return status_rank, f"{scope_rank}:{record.title.lower()}", str(record.metadata.get("id", ""))


def _bullet_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    return str(value)


def _render_record(record: CompilableRecord) -> list[str]:
    metadata = record.metadata
    tags = [str(tag) for tag in metadata.get("tags", []) if str(tag)]
    lines = [
        f"### {record.title}",
        "",
        f"- id: `{metadata.get('id')}`",
        f"- path: `{record.path}`",
        f"- kind: `{metadata.get('record_kind')}`",
        f"- scope/status: `{metadata.get('scope')}` / `{metadata.get('status')}`",
        f"- author: `{metadata.get('author')}`",
        f"- task_id: `{_bullet_value(metadata.get('task_id'))}`",
        f"- branch: `{_bullet_value(metadata.get('branch'))}`",
        f"- tags: `{_bullet_value(tags)}`",
        "",
        record.body,
        "",
    ]
    return lines


def _render_compile_markdown(
    *,
    config: MemoryConfig,
    target: str,
    records: list[CompilableRecord],
    user: str | None,
    task_id: str | None,
    branch: str | None,
    include_scopes: list[str],
    include_statuses: list[str],
    preferred_tags: list[str],
) -> str:
    title_by_target = {
        "runtime_digest": "Runtime Digest",
        "task_handoff": "Task Handoff",
        "system_digest": "System Digest",
        "publish_queue": "Publish Queue",
    }
    title = title_by_target[target]
    lines = [
        f"# {title}",
        "",
        "> Generated deterministically from Markdown + Front Matter records. This file is a rebuildable view, not truth source.",
        "",
        "## Filters",
        "",
        f"- target: `{target}`",
        f"- user: `{_bullet_value(user)}`",
        f"- task_id: `{_bullet_value(task_id)}`",
        f"- branch: `{_bullet_value(branch)}`",
        f"- include_scopes: `{_bullet_value(include_scopes)}`",
        f"- include_statuses: `{_bullet_value(include_statuses)}`",
        f"- preferred_tags: `{_bullet_value(preferred_tags)}`",
        "",
        "## Included Records",
        "",
    ]

    if target == "runtime_digest":
        legacy_lines = _legacy_memory_lines(config, user=user)
        if legacy_lines:
            lines.extend(["## Legacy Memory Files", ""])
            lines.extend(legacy_lines)
            lines.append("")

    if not records:
        lines.extend(["No records matched the compile filters.", ""])
    else:
        for record in records:
            lines.extend(_render_record(record))

    lines.extend(
        [
            "## Source References",
            "",
        ]
    )
    if not records:
        lines.append("- none")
    else:
        for record in records:
            lines.append(f"- `{record.metadata.get('id')}` -> `{record.path}`")
    lines.append("")
    return "\n".join(lines)


def _legacy_memory_lines(config: MemoryConfig, *, user: str | None) -> list[str]:
    rel_paths = ["memory-bank/activeContext.md", "memory-bank/progress.md"]
    lines: list[str] = []
    manager = PathManager(config)
    for rel_path in rel_paths:
        try:
            resolved = manager.resolve(rel_path, must_exist=True, must_be_file=True)
        except Exception:
            continue
        try:
            snippet = resolved.read_text(encoding="utf-8", errors="replace").strip().splitlines()[:6]
        except OSError:
            continue
        lines.append(f"### `{rel_path}`")
        lines.append("")
        lines.extend(f"> {line}" for line in snippet if line.strip())
        lines.append("")
    return lines


def _cache_key(target: str, *, user: str | None, task_id: str | None, branch: str | None) -> str:
    parts = [target]
    if task_id:
        parts.append(_slug(task_id, fallback="task"))
    elif branch:
        parts.append(_slug(branch, fallback="branch"))
    elif user:
        parts.append(_slug(user, fallback="user"))
    else:
        parts.append("system")
    return "-".join(parts) + ".json"


def _record_usage_stats(config: MemoryConfig, records: list[CompilableRecord], used_at: str) -> Path:
    """Persist compile usage stats to .ai-memory/usage-stats.json.

    The compiler must NOT mutate source record .md files (that would violate the
    "compiled output is rebuildable, sources are truth" invariant and pollute Git
    diffs / FTS index freshness). Usage stats live alongside the compile cache and
    can be deleted/rebuilt at any time.
    """
    stats_path = config.repo_root / ".ai-memory" / "usage-stats.json"
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if stats_path.is_file():
        try:
            data = json.loads(stats_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
    for record in records:
        record_id = str(record.metadata.get("id", ""))
        if not record_id:
            continue
        entry = data.get(record_id) if isinstance(data.get(record_id), dict) else {}
        entry["last_used_at"] = used_at
        entry["path"] = record.path
        data[record_id] = entry
    try:
        stats_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return stats_path


def get_record_last_used_at(config: MemoryConfig, record_id: str) -> str | None:
    """Return the most recent compile-time usage timestamp for a record id."""
    stats_path = config.repo_root / ".ai-memory" / "usage-stats.json"
    if not stats_path.is_file():
        return None
    try:
        data = json.loads(stats_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get(record_id)
    if isinstance(entry, dict):
        value = entry.get("last_used_at")
        return str(value) if value is not None else None
    return None


def memory_compile(
    config: MemoryConfig,
    *,
    target: str,
    user: str | None = None,
    task_id: str | None = None,
    branch: str | None = None,
    include_scopes: list[str] | None = None,
    include_statuses: list[str] | None = None,
    preferred_tags: list[str] | None = None,
) -> dict[str, Any]:
    if target not in SUPPORTED_TARGETS:
        return error_result("invalid_input", f"target must be one of: {', '.join(sorted(SUPPORTED_TARGETS))}")
    for name, value in {
        "include_scopes": include_scopes,
        "include_statuses": include_statuses,
        "preferred_tags": preferred_tags,
    }.items():
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(item, str) for item in value)
        ):
            return error_result("invalid_input", f"{name} must be a list of strings")

    effective_user = user or get_current_user(config.repo_root)
    if user is None and target in {"publish_queue", "system_digest"}:
        effective_user = None
    if effective_user == "unknown":
        effective_user = None
    scopes = [str(item) for item in (include_scopes or DEFAULT_INCLUDE_SCOPES)]
    if include_statuses is not None:
        statuses = [str(item) for item in include_statuses]
    elif target == "publish_queue":
        statuses = ["candidate"]
    else:
        statuses = [str(item) for item in DEFAULT_INCLUDE_STATUSES]
    tags = [str(item) for item in (preferred_tags or [])]
    if include_scopes is None and target == "system_digest":
        scopes = ["shared"]
    elif include_scopes is None and target == "publish_queue":
        scopes = ["shared", "personal"]

    try:
        records, scan_stats = _iter_records(config)
    except PathSecurityError as exc:
        return error_result("path_not_allowed", str(exc))
    except FileNotFoundError as exc:
        return error_result("not_found", str(exc))

    included = [
        record
        for record in records
        if _matches_filter(
            record,
            user=effective_user,
            task_id=task_id,
            branch=branch,
            include_scopes=scopes,
            include_statuses=statuses,
            preferred_tags=tags,
        )
    ]
    included.sort(key=_record_sort_key)

    content = _render_compile_markdown(
        config=config,
        target=target,
        records=included,
        user=effective_user,
        task_id=task_id,
        branch=branch,
        include_scopes=scopes,
        include_statuses=statuses,
        preferred_tags=tags,
    )

    rel_path = _compiled_path(target, user=effective_user, task_id=task_id, branch=branch)
    manager = PathManager(config)
    try:
        resolved = manager.resolve(rel_path, must_exist=False, must_be_file=False)
    except PathSecurityError as exc:
        return error_result("path_not_allowed", str(exc))

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except OSError as exc:
        return error_result("write_failed", f"failed to write compiled memory: {exc}")

    included_ids = [str(record.metadata.get("id")) for record in included]
    used_at = datetime.now(timezone.utc).isoformat()
    _record_usage_stats(config, included, used_at)
    cache_path = config.repo_root / ".ai-memory" / "compile-cache" / _cache_key(
        target,
        user=effective_user,
        task_id=task_id,
        branch=branch,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "target": target,
                "path": rel_path,
                "included_record_ids": included_ids,
                "included_record_paths": [record.path for record in included],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    append_event(
        config,
        "memory_compile",
        {
            "target": target,
            "path": rel_path,
            "user": effective_user,
            "task_id": task_id,
            "branch": branch,
            "include_scopes": scopes,
            "include_statuses": statuses,
            "preferred_tags": tags,
            "included_record_ids": included_ids,
        },
    )

    return ok_result(
        "memory compiled",
        target=target,
        path=rel_path,
        content=content,
        included_record_ids=included_ids,
        included_record_paths=[record.path for record in included],
        stats={
            **scan_stats,
            "matched_records": len(included),
        },
    )


def memory_get_runtime_digest(
    config: MemoryConfig,
    *,
    user: str | None = None,
    task_id: str | None = None,
    branch: str | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    if max_chars is not None and max_chars < 0:
        return error_result("invalid_input", "max_chars must be >= 0")

    effective_user = user or get_current_user(config.repo_root)
    if effective_user == "unknown":
        effective_user = None
    rel_path = _compiled_path("runtime_digest", user=effective_user, task_id=task_id, branch=branch)
    manager = PathManager(config)
    try:
        resolved = manager.resolve(rel_path, must_exist=True, must_be_file=True)
    except PathSecurityError as exc:
        return error_result("path_not_allowed", str(exc))
    except FileNotFoundError:
        return error_result("not_found", f"runtime digest does not exist: {rel_path}. Run memory_compile first.", path=rel_path)
    except IsADirectoryError as exc:
        return error_result("invalid_path", str(exc))

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return error_result("read_failed", f"failed to read runtime digest: {exc}")

    truncated = False
    if max_chars is not None:
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

    return ok_result(
        "runtime digest read",
        path=rel_path,
        content=content,
        truncated=truncated,
    )
