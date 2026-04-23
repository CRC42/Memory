from __future__ import annotations

import re
import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig
from .memory_events import append_event, get_current_user
from .memory_paths import PathManager, PathSecurityError
from .memory_records import parse_record_markdown, render_record_markdown
from .memory_record_io import iter_parsed_records
from .memory_result import error_result, ok_result
from .memory_scoring import build_reference_counts, load_usage_stats, parse_timestamp, score_record

STANDARD_TARGETS = {"runtime_digest", "task_handoff", "system_digest", "publish_queue"}
SNAPSHOT_TARGETS = {"daily_snapshot", "weekly_snapshot", "monthly_snapshot"}
ROLE_TARGETS = {"rollback_context", "review_queue", "dao_digest", "fa_digest", "shu_digest"}
SUPPORTED_TARGETS = STANDARD_TARGETS | SNAPSHOT_TARGETS | ROLE_TARGETS
SUPPORTED_BODY_MODES = {"compact", "full"}
DEFAULT_BODY_MODE = "compact"
DEFAULT_INCLUDE_SCOPES = ["shared", "personal"]
DEFAULT_INCLUDE_STATUSES = ["validated", "published"]
DEFAULT_CONTEXT_SCOPES = ["shared", "personal", "session", "task_or_branch", "project_shared", "org_shared"]
COMPACT_SECTION_PRIORITY = [
    "decision",
    "expected behavior",
    "acceptance checks",
    "next step",
    "next steps",
    "notes",
    "details",
]
COMPACT_BODY_CHAR_LIMIT = 600
DIGEST_LEVELS = {
    "dao_digest": "dao",
    "fa_digest": "fa",
    "shu_digest": "shu",
}


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
    if target == "daily_snapshot":
        label = _slug(task_id or "daily", fallback="daily")
        return f"memory-bank/compiled/snapshots/daily/{label}.md"
    if target == "weekly_snapshot":
        label = _slug(task_id or "weekly", fallback="weekly")
        return f"memory-bank/compiled/snapshots/weekly/{label}.md"
    if target == "monthly_snapshot":
        label = _slug(task_id or "monthly", fallback="monthly")
        return f"memory-bank/compiled/snapshots/monthly/{label}.md"
    if target == "review_queue":
        return "memory-bank/compiled/review/review-queue.md"
    if target == "dao_digest":
        return "memory-bank/compiled/runtime/dao-digest.md"
    if target == "fa_digest":
        return "memory-bank/compiled/runtime/fa-digest.md"
    if target == "shu_digest":
        return "memory-bank/compiled/runtime/shu-digest.md"
    if target == "rollback_context":
        if task_id:
            return f"memory-bank/compiled/runtime/task/{_slug(task_id, fallback='task')}-rollback.md"
        if branch:
            return f"memory-bank/compiled/runtime/branch/{_slug(branch, fallback='branch')}-rollback.md"
        if user:
            return f"memory-bank/compiled/runtime/people/{_slug(user, fallback='user')}-rollback.md"
        return "memory-bank/compiled/runtime/rollback-context.md"
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
    """Project shared parsed records into CompilableRecord and pass through stats."""
    parsed, stats = iter_parsed_records(config)
    records = [
        CompilableRecord(
            path=record.rel_path,
            metadata=record.metadata,
            body=record.body.strip(),
            title=_first_heading(record.body),
        )
        for record in parsed
    ]
    return records, stats


def _record_time(record: CompilableRecord) -> datetime | None:
    metadata = record.metadata
    for key in ("occurred_at", "valid_from", "updated_at", "created_at"):
        parsed = parse_timestamp(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def _reference_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = parse_timestamp(value)
    if parsed is not None:
        return parsed
    raise ValueError(f"invalid ISO timestamp/date: {value}")


def _time_window(target: str, as_of: datetime) -> tuple[datetime, datetime, str]:
    end = as_of
    if target == "daily_snapshot":
        start = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1) - timedelta(microseconds=1)
        label = start.strftime("%Y-%m-%d")
        return start, end, label
    if target == "weekly_snapshot":
        start = (as_of - timedelta(days=as_of.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7) - timedelta(microseconds=1)
        iso_year, iso_week, _ = start.isocalendar()
        label = f"{iso_year}-W{iso_week:02d}"
        return start, end, label
    if target == "monthly_snapshot":
        start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            month_end = start.replace(year=start.year + 1, month=1)
        else:
            month_end = start.replace(month=start.month + 1)
        end = month_end - timedelta(microseconds=1)
        label = start.strftime("%Y-%m")
        return start, end, label
    raise ValueError(f"unsupported snapshot target: {target}")


def load_compile_cache_entries(
    config: MemoryConfig,
    *,
    targets: set[str] | None = None,
) -> list[dict[str, Any]]:
    cache_dir = config.repo_root / ".ai-memory" / "compile-cache"
    if not cache_dir.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(cache_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        target = str(raw.get("target", ""))
        if targets is not None and target not in targets:
            continue
        raw["cache_path"] = str(path.relative_to(config.repo_root)).replace("\\", "/")
        entries.append(raw)
    return entries


def find_compile_cache_entry(config: MemoryConfig, compiled_path: str) -> dict[str, Any] | None:
    normalized = compiled_path.replace("\\", "/")
    for entry in load_compile_cache_entries(config):
        if str(entry.get("path", "")).replace("\\", "/") == normalized:
            return entry
    return None


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


def _clip_text(text: str, limit: int = COMPACT_BODY_CHAR_LIMIT) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[:limit].rstrip()
    if "\n" in clipped:
        clipped = clipped.rsplit("\n", 1)[0].rstrip() or clipped
    elif " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0].rstrip() or clipped
    return clipped + "\n\n..."


def _body_without_title(body: str, title: str) -> str:
    lines = body.strip().splitlines()
    if lines and lines[0].strip().startswith("#"):
        heading = lines[0].strip().lstrip("#").strip()
        if heading == title:
            return "\n".join(lines[1:]).strip()
    return body.strip()


def _markdown_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if current_key is not None:
                sections[current_key] = current_lines
            current_key = stripped.lstrip("#").strip().lower()
            current_lines = [stripped]
        elif current_key is not None:
            current_lines.append(line)
    if current_key is not None:
        sections[current_key] = current_lines

    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _compact_body(record: CompilableRecord) -> str:
    body = _body_without_title(record.body, record.title)
    sections = _markdown_sections(body)
    selected: list[str] = []

    for heading in COMPACT_SECTION_PRIORITY:
        if heading in sections:
            selected.append(sections[heading])
        if len("\n\n".join(selected)) >= COMPACT_BODY_CHAR_LIMIT:
            break

    if selected:
        return _clip_text("\n\n".join(selected))

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    if paragraphs:
        return _clip_text("\n\n".join(paragraphs[:2]))
    return "_No compact content extracted._"


def _render_record(record: CompilableRecord, *, body_mode: str) -> list[str]:
    metadata = record.metadata
    if body_mode == "full":
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
    else:
        lines = [
            f"### {record.title}",
            "",
            f"- id: `{metadata.get('id')}`",
            f"- source: `{record.path}`",
            f"- status: `{metadata.get('status')}`",
            "",
            _compact_body(record),
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
    body_mode: str,
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
        f"- body_mode: `{body_mode}`",
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
            lines.extend(_render_record(record, body_mode=body_mode))

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


def _cache_key(
    target: str,
    *,
    user: str | None,
    task_id: str | None,
    branch: str | None,
    hint: str | None = None,
) -> str:
    parts = [target]
    if hint:
        parts.append(_slug(hint, fallback="entry"))
    elif task_id:
        parts.append(_slug(task_id, fallback="task"))
    elif branch:
        parts.append(_slug(branch, fallback="branch"))
    elif user:
        parts.append(_slug(user, fallback="user"))
    else:
        parts.append("system")
    return "-".join(parts) + ".json"


def _record_usage_stats(config: MemoryConfig, records: list[CompilableRecord], used_at: str, *, target: str) -> Path:
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
        entry["compile_hit_count"] = int(entry.get("compile_hit_count", 0) or 0) + 1
        compile_targets = entry.get("compile_targets")
        if not isinstance(compile_targets, list):
            compile_targets = []
        normalized_targets = [str(item) for item in compile_targets if str(item).strip()]
        if target not in normalized_targets:
            normalized_targets.append(target)
        entry["compile_targets"] = normalized_targets
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


def _record_sort_with_score(record: CompilableRecord, score_data: dict[str, Any]) -> tuple[float, float, str]:
    timestamp = _record_time(record)
    epoch = timestamp.timestamp() if timestamp is not None else 0.0
    return (-float(score_data.get("total", 0.0)), -epoch, record.title.lower())


def _scored_records(config: MemoryConfig, records: list[CompilableRecord]) -> list[tuple[CompilableRecord, dict[str, Any]]]:
    usage_stats = load_usage_stats(config)
    reference_counts = build_reference_counts(records)
    now = datetime.now(timezone.utc)
    scored = [
        (
            record,
            score_record(
                record.metadata,
                usage_entry=usage_stats.get(str(record.metadata.get("id", "")), {}),
                reference_count=reference_counts.get(str(record.metadata.get("id", "")), 0),
                now=now,
            ),
        )
        for record in records
    ]
    scored.sort(key=lambda item: _record_sort_with_score(item[0], item[1]))
    return scored


def _summary_with_score(record: CompilableRecord, score_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(record.metadata.get("id", "")),
        "title": record.title,
        "path": record.path,
        "record_kind": record.metadata.get("record_kind"),
        "scope": record.metadata.get("scope"),
        "status": record.metadata.get("status"),
        "cognitive_level": record.metadata.get("cognitive_level"),
        "memory_tier": score_data.get("effective_memory_tier"),
        "importance_score": score_data.get("total"),
    }


def _write_compiled_view(
    config: MemoryConfig,
    *,
    target: str,
    rel_path: str,
    content: str,
    included: list[CompilableRecord],
    user: str | None,
    task_id: str | None,
    branch: str | None,
    body_mode: str,
    cache_hint: str | None = None,
    cache_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    _record_usage_stats(config, included, used_at, target=target)
    cache_path = config.repo_root / ".ai-memory" / "compile-cache" / _cache_key(
        target,
        user=user,
        task_id=task_id,
        branch=branch,
        hint=cache_hint,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_payload = {
        "target": target,
        "path": rel_path,
        "included_record_ids": included_ids,
        "included_record_paths": [record.path for record in included],
        "body_mode": body_mode,
    }
    if cache_extra:
        cache_payload.update(cache_extra)
    cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_event(
        config,
        "memory_compile",
        {
            "target": target,
            "path": rel_path,
            "user": user,
            "task_id": task_id,
            "branch": branch,
            "body_mode": body_mode,
            "included_record_ids": included_ids,
        },
    )
    return ok_result(
        "memory compiled",
        target=target,
        path=rel_path,
        content=content,
        body_mode=body_mode,
        included_record_ids=included_ids,
        included_record_paths=[record.path for record in included],
    )


def _format_record_bullets(records: list[tuple[CompilableRecord, dict[str, Any]]], *, limit: int = 10) -> list[str]:
    if not records:
        return ["- none"]
    lines: list[str] = []
    for record, score_data in records[:limit]:
        lines.append(
            f"- `{record.metadata.get('id')}` | {record.title} | "
            f"{record.metadata.get('record_kind')} | score={score_data.get('total')} | "
            f"tier={score_data.get('effective_memory_tier')}"
        )
    return lines


def _compile_snapshot_target(
    config: MemoryConfig,
    *,
    target: str,
    records: list[CompilableRecord],
    user: str | None,
    task_id: str | None,
    branch: str | None,
    body_mode: str,
    as_of: str | None,
) -> dict[str, Any]:
    try:
        reference = _reference_time(as_of)
    except ValueError as exc:
        return error_result("invalid_input", str(exc))
    window_start, window_end, label = _time_window(target, reference)
    in_window = [
        record
        for record in records
        if (timestamp := _record_time(record)) is not None and window_start <= timestamp <= window_end
    ]
    scored = _scored_records(config, in_window)
    top_changes = [
        item
        for item in scored
        if str(item[0].metadata.get("record_kind")) in {"decision", "procedure", "incident", "system_rule"}
    ]
    top_reused = sorted(
        scored,
        key=lambda item: (
            -int(item[1].get("usage", {}).get("compile_hit_count", 0)),
            -float(item[1].get("total", 0.0)),
            item[0].title.lower(),
        ),
    )
    open_items = [
        item
        for item in scored
        if str(item[0].metadata.get("status")) in {"raw", "candidate", "degraded"}
        or bool(item[0].metadata.get("conflicts_with"))
    ]
    cache_targets = {"daily_snapshot"} if target == "weekly_snapshot" else {"weekly_snapshot"} if target == "monthly_snapshot" else set()
    derived_snapshots: list[str] = []
    if cache_targets:
        for entry in load_compile_cache_entries(config, targets=cache_targets):
            entry_start = parse_timestamp(entry.get("window_start"))
            if entry_start is None or not (window_start <= entry_start <= window_end):
                continue
            snapshot_id = str(entry.get("snapshot_id", "")).strip()
            if snapshot_id:
                derived_snapshots.append(snapshot_id)
    snapshot_id = f"{target}:{label}"
    title = {
        "daily_snapshot": f"Daily Snapshot {label}",
        "weekly_snapshot": f"Weekly Snapshot {label}",
        "monthly_snapshot": f"Monthly Snapshot {label}",
    }[target]
    lines = [
        f"# {title}",
        "",
        "> Generated deterministically from source records. This snapshot is rebuildable and does not mutate source memory.",
        "",
        "## Window",
        "",
        f"- snapshot_id: `{snapshot_id}`",
        f"- window_start: `{window_start.isoformat()}`",
        f"- window_end: `{window_end.isoformat()}`",
        f"- matched_records: `{len(in_window)}`",
        "",
        "## Derived From Records",
        "",
    ]
    lines.extend(f"- `{record.metadata.get('id')}` -> `{record.path}`" for record in in_window[:20] or [])
    if not in_window:
        lines.append("- none")
    lines.extend(["", "## Top Changes", ""])
    lines.extend(_format_record_bullets(top_changes, limit=10))
    lines.extend(["", "## Top Reused Memories", ""])
    lines.extend(_format_record_bullets(top_reused, limit=10))
    lines.extend(["", "## Open Questions", ""])
    lines.extend(_format_record_bullets(open_items, limit=10))
    candidate_heading = "## Candidate For Weekly" if target == "daily_snapshot" else "## Candidate For Monthly"
    lines.extend(["", candidate_heading, ""])
    lines.extend(_format_record_bullets(scored, limit=10))
    if target != "daily_snapshot":
        lines.extend(["", "## Derived From Snapshots", ""])
        if derived_snapshots:
            lines.extend(f"- `{snapshot}`" for snapshot in derived_snapshots)
        else:
            lines.append("- none")
    lines.append("")
    rel_path = _compiled_path(target, task_id=label)
    result = _write_compiled_view(
        config,
        target=target,
        rel_path=rel_path,
        content="\n".join(lines),
        included=in_window,
        user=user,
        task_id=task_id,
        branch=branch,
        body_mode=body_mode,
        cache_hint=label,
        cache_extra={
            "snapshot_id": snapshot_id,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "derived_from_snapshot_ids": derived_snapshots,
            "summary": {
                "top_changes": [_summary_with_score(record, score_data) for record, score_data in top_changes[:5]],
                "top_reused_memories": [_summary_with_score(record, score_data) for record, score_data in top_reused[:5]],
                "open_questions": [_summary_with_score(record, score_data) for record, score_data in open_items[:5]],
            },
        },
    )
    if result.get("ok"):
        result["snapshot_id"] = snapshot_id
        result["window_start"] = window_start.isoformat()
        result["window_end"] = window_end.isoformat()
        result["derived_from_snapshot_ids"] = derived_snapshots
    return result


def _compile_level_digest(
    config: MemoryConfig,
    *,
    target: str,
    records: list[CompilableRecord],
    user: str | None,
    task_id: str | None,
    branch: str | None,
    body_mode: str,
) -> dict[str, Any]:
    level = DIGEST_LEVELS[target]
    filtered = [
        record
        for record in records
        if str(record.metadata.get("cognitive_level", "")) == level
        and str(record.metadata.get("status", "")) in {"validated", "published"}
    ]
    scored = _scored_records(config, filtered)
    title = f"{level.upper()} Digest"
    lines = [
        f"# {title}",
        "",
        "> Deterministic cognitive-level digest.",
        "",
        f"- level: `{level}`",
        f"- records: `{len(filtered)}`",
        "",
        "## Included Records",
        "",
    ]
    for record, score_data in scored:
        lines.extend(
            [
                f"### {record.title}",
                "",
                f"- id: `{record.metadata.get('id')}`",
                f"- path: `{record.path}`",
                f"- status: `{record.metadata.get('status')}`",
                f"- importance_score: `{score_data.get('total')}`",
                "",
                _compact_body(record),
                "",
            ]
        )
    if not scored:
        lines.append("No records matched the compile filters.\n")
    return _write_compiled_view(
        config,
        target=target,
        rel_path=_compiled_path(target),
        content="\n".join(lines),
        included=filtered,
        user=user,
        task_id=task_id,
        branch=branch,
        body_mode=body_mode,
    )


def _compile_review_queue(
    config: MemoryConfig,
    *,
    records: list[CompilableRecord],
    user: str | None,
    task_id: str | None,
    branch: str | None,
    body_mode: str,
) -> dict[str, Any]:
    scored = _scored_records(
        config,
        [
            record
            for record in records
            if str(record.metadata.get("status", "")) in {"raw", "candidate", "validated", "published", "degraded"}
        ],
    )
    hottest = [item for item in scored if item[1].get("effective_memory_tier") == "hot"]
    newest_rules = [
        item
        for item in scored
        if str(item[0].metadata.get("record_kind")) in {"decision", "procedure", "system_rule"}
    ]
    discarded = [item for item in scored if str(item[0].metadata.get("status")) in {"degraded", "archived"}]
    lines = [
        "# Review Queue",
        "",
        "> Deterministic review queue ordered by governance, usage, impact, novelty, conflict, and decay.",
        "",
        "## Most Important Now",
        "",
    ]
    lines.extend(_format_record_bullets(scored, limit=10))
    lines.extend(["", "## Hot Tier", ""])
    lines.extend(_format_record_bullets(hottest, limit=10))
    lines.extend(["", "## New Stable Rules", ""])
    lines.extend(_format_record_bullets(newest_rules, limit=10))
    lines.extend(["", "## Discarded Paths", ""])
    lines.extend(_format_record_bullets(discarded, limit=10))
    lines.append("")
    result = _write_compiled_view(
        config,
        target="review_queue",
        rel_path=_compiled_path("review_queue"),
        content="\n".join(lines),
        included=[record for record, _score_data in scored[:20]],
        user=user,
        task_id=task_id,
        branch=branch,
        body_mode=body_mode,
    )
    if result.get("ok"):
        result["ranked"] = [_summary_with_score(record, score_data) for record, score_data in scored[:10]]
    return result


def _compile_rollback_context(
    config: MemoryConfig,
    *,
    records: list[CompilableRecord],
    user: str | None,
    task_id: str | None,
    branch: str | None,
    body_mode: str,
) -> dict[str, Any]:
    scoped = [
        record
        for record in records
        if (task_id is None or record.metadata.get("task_id") == task_id)
        and (branch is None or record.metadata.get("branch") in (None, branch))
    ]
    rollback_candidates = [
        record
        for record in scoped
        if str(record.metadata.get("record_kind", "")) in {"incident", "decision", "procedure"}
        or bool(record.metadata.get("supersedes"))
        or bool(record.metadata.get("conflicts_with"))
    ]
    scored = _scored_records(config, rollback_candidates)
    lines = [
        "# Rollback Context",
        "",
        "> Deterministic rollback-oriented context view.",
        "",
        f"- task_id: `{_bullet_value(task_id)}`",
        f"- branch: `{_bullet_value(branch)}`",
        "",
        "## Rollback Chain",
        "",
    ]
    lines.extend(_format_record_bullets(scored, limit=15))
    lines.extend(["", "## Source References", ""])
    if scored:
        lines.extend(f"- `{record.metadata.get('id')}` -> `{record.path}`" for record, _score_data in scored[:15])
    else:
        lines.append("- none")
    lines.append("")
    result = _write_compiled_view(
        config,
        target="rollback_context",
        rel_path=_compiled_path("rollback_context", user=user, task_id=task_id, branch=branch),
        content="\n".join(lines),
        included=rollback_candidates,
        user=user,
        task_id=task_id,
        branch=branch,
        body_mode=body_mode,
    )
    if result.get("ok"):
        result["ranked"] = [_summary_with_score(record, score_data) for record, score_data in scored[:10]]
    return result


def memory_compare_snapshots(config: MemoryConfig, *, path: str, other_path: str) -> dict[str, Any]:
    left = find_compile_cache_entry(config, path)
    right = find_compile_cache_entry(config, other_path)
    if left is None:
        return error_result("not_found", f"compiled snapshot metadata not found: {path}", path=path)
    if right is None:
        return error_result("not_found", f"compiled snapshot metadata not found: {other_path}", path=other_path)
    left_ids = {str(item) for item in left.get("included_record_ids", []) if str(item).strip()}
    right_ids = {str(item) for item in right.get("included_record_ids", []) if str(item).strip()}
    try:
        records, _stats = _iter_records(config)
    except (PathSecurityError, FileNotFoundError) as exc:
        return error_result("path_error", str(exc))
    by_id = {str(record.metadata.get("id", "")): record for record in records}

    def summarize(record_id: str) -> dict[str, Any]:
        record = by_id.get(record_id)
        if record is None:
            return {"id": record_id, "path": None, "title": None}
        return {"id": record_id, "path": record.path, "title": record.title, "record_kind": record.metadata.get("record_kind")}

    added = [summarize(record_id) for record_id in sorted(right_ids - left_ids)]
    removed = [summarize(record_id) for record_id in sorted(left_ids - right_ids)]
    persisted = [summarize(record_id) for record_id in sorted(left_ids & right_ids)]
    return ok_result(
        "snapshots compared",
        left={"path": path, "snapshot_id": left.get("snapshot_id"), "target": left.get("target")},
        right={"path": other_path, "snapshot_id": right.get("snapshot_id"), "target": right.get("target")},
        added=added,
        removed=removed,
        persisted=persisted,
        stats={
            "left_records": len(left_ids),
            "right_records": len(right_ids),
            "added": len(added),
            "removed": len(removed),
            "persisted": len(persisted),
        },
    )


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
    body_mode: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    if target not in SUPPORTED_TARGETS:
        return error_result("invalid_input", f"target must be one of: {', '.join(sorted(SUPPORTED_TARGETS))}")
    effective_body_mode = body_mode or DEFAULT_BODY_MODE
    if effective_body_mode not in SUPPORTED_BODY_MODES:
        return error_result("invalid_input", f"body_mode must be one of: {', '.join(sorted(SUPPORTED_BODY_MODES))}")
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

    if target in SNAPSHOT_TARGETS:
        result = _compile_snapshot_target(
            config,
            target=target,
            records=records,
            user=effective_user,
            task_id=task_id,
            branch=branch,
            body_mode=effective_body_mode,
            as_of=as_of,
        )
        if result.get("ok"):
            result["stats"] = {**scan_stats, "matched_records": len(result.get("included_record_ids", []))}
        return result

    if target in DIGEST_LEVELS:
        result = _compile_level_digest(
            config,
            target=target,
            records=records,
            user=effective_user,
            task_id=task_id,
            branch=branch,
            body_mode=effective_body_mode,
        )
        if result.get("ok"):
            result["stats"] = {**scan_stats, "matched_records": len(result.get("included_record_ids", []))}
        return result

    if target == "review_queue":
        result = _compile_review_queue(
            config,
            records=records,
            user=effective_user,
            task_id=task_id,
            branch=branch,
            body_mode=effective_body_mode,
        )
        if result.get("ok"):
            result["stats"] = {**scan_stats, "matched_records": len(result.get("included_record_ids", []))}
        return result

    if target == "rollback_context":
        result = _compile_rollback_context(
            config,
            records=records,
            user=effective_user,
            task_id=task_id,
            branch=branch,
            body_mode=effective_body_mode,
        )
        if result.get("ok"):
            result["stats"] = {**scan_stats, "matched_records": len(result.get("included_record_ids", []))}
        return result

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
        body_mode=effective_body_mode,
    )

    result = _write_compiled_view(
        config,
        target=target,
        rel_path=_compiled_path(target, user=effective_user, task_id=task_id, branch=branch),
        content=content,
        included=included,
        user=effective_user,
        task_id=task_id,
        branch=branch,
        body_mode=effective_body_mode,
        cache_extra={
            "include_scopes": scopes,
            "include_statuses": statuses,
            "preferred_tags": tags,
        },
    )
    if result.get("ok"):
        result["stats"] = {**scan_stats, "matched_records": len(included)}
    return result


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
