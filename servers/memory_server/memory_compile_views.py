"""Snapshot / digest / review / rollback compile views.

Extracted from `memory_compiler.py` (P0-1 follow-up). Each function takes
the already-collected record list (compiler does the corpus scan and
filter dispatch) and returns the same `ok/error` envelope the dispatch
layer expects. All Markdown rendering and bullet formatting lives here so
the compiler can stay an orchestration shell.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .memory_compile_scoring import (
    record_time as _record_time,
    scored_records as _scored_records,
    summary_with_score as _summary_with_score,
)
from .memory_compile_targets import DIGEST_LEVELS, compiled_path as _compiled_path
from .memory_compile_writer import write_compiled_view
from .memory_compile_render import bullet_value as _bullet_value
from .memory_compiler_cache import find_compile_cache_entry, load_compile_cache_entries
from .memory_config import MemoryConfig
from .memory_corpus import CompilableRecord, compact_body as _compact_body, iter_compilable_records
from .memory_paths import PathSecurityError
from .memory_result import error_result, ok_result
from .memory_scoring import parse_timestamp


# ── Time helpers ───────────────────────────────────────────────────────


def reference_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = parse_timestamp(value)
    if parsed is not None:
        return parsed
    raise ValueError(f"invalid ISO timestamp/date: {value}")


def time_window(target: str, as_of: datetime) -> tuple[datetime, datetime, str]:
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


# ── Shared bullet renderer ─────────────────────────────────────────────


def format_record_bullets(
    records: list[tuple[CompilableRecord, dict[str, Any]]], *, limit: int = 10
) -> list[str]:
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


# ── Snapshot target ────────────────────────────────────────────────────


def compile_snapshot_target(
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
        reference = reference_time(as_of)
    except ValueError as exc:
        return error_result("invalid_input", str(exc))
    window_start, window_end, label = time_window(target, reference)
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
    cache_targets = (
        {"daily_snapshot"}
        if target == "weekly_snapshot"
        else {"weekly_snapshot"}
        if target == "monthly_snapshot"
        else set()
    )
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
    lines.extend(format_record_bullets(top_changes, limit=10))
    lines.extend(["", "## Top Reused Memories", ""])
    lines.extend(format_record_bullets(top_reused, limit=10))
    lines.extend(["", "## Open Questions", ""])
    lines.extend(format_record_bullets(open_items, limit=10))
    candidate_heading = "## Candidate For Weekly" if target == "daily_snapshot" else "## Candidate For Monthly"
    lines.extend(["", candidate_heading, ""])
    lines.extend(format_record_bullets(scored, limit=10))
    if target != "daily_snapshot":
        lines.extend(["", "## Derived From Snapshots", ""])
        if derived_snapshots:
            lines.extend(f"- `{snapshot}`" for snapshot in derived_snapshots)
        else:
            lines.append("- none")
    lines.append("")
    rel_path = _compiled_path(target, task_id=label)
    result = write_compiled_view(
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
                "top_reused_memories": [
                    _summary_with_score(record, score_data) for record, score_data in top_reused[:5]
                ],
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


# ── dao / fa / shu cognitive-level digests ────────────────────────────


def compile_level_digest(
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
    return write_compiled_view(
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


# ── Review queue ──────────────────────────────────────────────────────


def compile_review_queue(
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
    lines.extend(format_record_bullets(scored, limit=10))
    lines.extend(["", "## Hot Tier", ""])
    lines.extend(format_record_bullets(hottest, limit=10))
    lines.extend(["", "## New Stable Rules", ""])
    lines.extend(format_record_bullets(newest_rules, limit=10))
    lines.extend(["", "## Discarded Paths", ""])
    lines.extend(format_record_bullets(discarded, limit=10))
    lines.append("")
    result = write_compiled_view(
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


# ── Rollback context ──────────────────────────────────────────────────


def compile_rollback_context(
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
    lines.extend(format_record_bullets(scored, limit=15))
    lines.extend(["", "## Source References", ""])
    if scored:
        lines.extend(f"- `{record.metadata.get('id')}` -> `{record.path}`" for record, _score_data in scored[:15])
    else:
        lines.append("- none")
    lines.append("")
    result = write_compiled_view(
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


# ── Snapshot diff ─────────────────────────────────────────────────────


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
        records, _stats = iter_compilable_records(config)
    except (PathSecurityError, FileNotFoundError) as exc:
        return error_result("path_error", str(exc))
    by_id = {str(record.metadata.get("id", "")): record for record in records}

    def summarize(record_id: str) -> dict[str, Any]:
        record = by_id.get(record_id)
        if record is None:
            return {"id": record_id, "path": None, "title": None}
        return {
            "id": record_id,
            "path": record.path,
            "title": record.title,
            "record_kind": record.metadata.get("record_kind"),
        }

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


__all__ = [
    "reference_time",
    "time_window",
    "format_record_bullets",
    "compile_snapshot_target",
    "compile_level_digest",
    "compile_review_queue",
    "compile_rollback_context",
    "memory_compare_snapshots",
]
