from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .memory_compiler import CompilableRecord, _compact_body, _iter_records, load_compile_cache_entries
from .memory_config import MemoryConfig
from .memory_lineage import memory_list_conflicts
from .memory_paths import PathSecurityError
from .memory_result import error_result, ok_result
from .memory_scoring import build_reference_counts, load_usage_stats, parse_timestamp, score_record

DEFAULT_RETRIEVAL_SCOPES = ["shared", "personal", "session", "task_or_branch", "project_shared", "org_shared"]
FACET_FIELDS = [
    "asset_paths",
    "map_names",
    "plugin_names",
    "module_names",
    "class_names",
    "blueprint_paths",
]


def _normalize_list(value: list[str] | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _record_time(record: CompilableRecord) -> datetime | None:
    for key in ("occurred_at", "valid_from", "updated_at", "created_at"):
        parsed = parse_timestamp(record.metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def _text_blob(record: CompilableRecord) -> str:
    metadata = record.metadata
    parts = [
        record.title,
        record.body,
        str(metadata.get("record_kind", "")),
        str(metadata.get("scope", "")),
        str(metadata.get("status", "")),
        str(metadata.get("system_area", "")),
        " ".join(str(item) for item in metadata.get("tags", []) if str(item)),
        " ".join(str(item) for item in metadata.get("source_refs", []) if str(item)),
    ]
    return "\n".join(parts).lower()


def _query_match_score(record: CompilableRecord, query: str | None) -> float:
    if not query:
        return 0.0
    terms = [term for term in re.split(r"\s+", query.lower().strip()) if term]
    if not terms:
        return 0.0
    blob = _text_blob(record)
    hits = sum(1 for term in terms if term in blob)
    if hits == 0:
        return -1.0
    title_bonus = 0.2 if any(term in record.title.lower() for term in terms) else 0.0
    return min(0.4, hits / len(terms) * 0.25 + title_bonus)


def _matches_facets(
    record: CompilableRecord,
    *,
    system_area: str | None,
    facet_filters: dict[str, list[str]],
) -> bool:
    if system_area and str(record.metadata.get("system_area", "")) != system_area:
        return False
    for field, expected_values in facet_filters.items():
        if not expected_values:
            continue
        current = record.metadata.get(field)
        if not isinstance(current, list):
            return False
        current_values = {str(item) for item in current if str(item).strip()}
        if not current_values.intersection(expected_values):
            return False
    return True


def _summary(record: CompilableRecord, score_data: dict[str, Any], *, include_body: bool = False) -> dict[str, Any]:
    result = {
        "id": str(record.metadata.get("id", "")),
        "title": record.title,
        "path": record.path,
        "record_kind": record.metadata.get("record_kind"),
        "scope": record.metadata.get("scope"),
        "status": record.metadata.get("status"),
        "cognitive_level": record.metadata.get("cognitive_level"),
        "memory_tier": score_data.get("effective_memory_tier"),
        "importance_score": score_data.get("total"),
        "system_area": record.metadata.get("system_area"),
    }
    if include_body:
        result["body"] = _compact_body(record)
    return result


def _next_steps(records: list[tuple[CompilableRecord, dict[str, Any]]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for record, score_data in records:
        body = record.body
        lowered = body.lower()
        if "next step" not in lowered and "next steps" not in lowered:
            continue
        lines = [line.strip("- ").strip() for line in body.splitlines() if line.strip().startswith("-")]
        steps.append(
            {
                "record": _summary(record, score_data),
                "steps": lines[:5] if lines else [_compact_body(record)],
            }
        )
        if len(steps) >= 5:
            break
    return steps


def _recent_snapshots(config: MemoryConfig, *, limit: int = 5) -> list[dict[str, Any]]:
    entries = load_compile_cache_entries(config, targets={"daily_snapshot", "weekly_snapshot", "monthly_snapshot"})
    entries.sort(key=lambda item: str(item.get("window_end", "")), reverse=True)
    return [
        {
            "snapshot_id": entry.get("snapshot_id"),
            "target": entry.get("target"),
            "path": entry.get("path"),
            "window_start": entry.get("window_start"),
            "window_end": entry.get("window_end"),
            "record_count": len(entry.get("included_record_ids", []) or []),
        }
        for entry in entries[:limit]
    ]


def memory_retrieve_context(
    config: MemoryConfig,
    *,
    query: str | None = None,
    user: str | None = None,
    task_id: str | None = None,
    branch: str | None = None,
    include_scopes: list[str] | None = None,
    include_statuses: list[str] | None = None,
    preferred_tags: list[str] | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    system_area: str | None = None,
    asset_paths: list[str] | None = None,
    map_names: list[str] | None = None,
    plugin_names: list[str] | None = None,
    module_names: list[str] | None = None,
    class_names: list[str] | None = None,
    blueprint_paths: list[str] | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    limit = top_k or 10
    if limit <= 0:
        return error_result("invalid_input", "top_k must be >= 1")
    parsed_start = parse_timestamp(window_start) if window_start else None
    parsed_end = parse_timestamp(window_end) if window_end else None
    if window_start and parsed_start is None:
        return error_result("invalid_input", f"invalid window_start: {window_start}")
    if window_end and parsed_end is None:
        return error_result("invalid_input", f"invalid window_end: {window_end}")
    if parsed_start and parsed_end and parsed_start > parsed_end:
        return error_result("invalid_input", "window_start must be <= window_end")

    try:
        records, scan_stats = _iter_records(config)
    except (PathSecurityError, FileNotFoundError) as exc:
        return error_result("path_error", str(exc))

    scopes = set(str(item) for item in (include_scopes or DEFAULT_RETRIEVAL_SCOPES))
    statuses = set(str(item) for item in (include_statuses or ["raw", "candidate", "validated", "published", "degraded"]))
    tags = set(_normalize_list(preferred_tags))
    scoped = [
        record
        for record in records
        if str(record.metadata.get("scope", "")) in scopes
        and str(record.metadata.get("status", "")) in statuses
        and (not user or str(record.metadata.get("scope", "")) != "personal" or str(record.metadata.get("author", "")) == user)
        and (not task_id or record.metadata.get("task_id") in (None, task_id))
        and (not branch or record.metadata.get("branch") in (None, branch))
        and (not tags or tags.intersection({str(item) for item in record.metadata.get("tags", []) if str(item)}))
    ]

    time_filtered = []
    for record in scoped:
        timestamp = _record_time(record)
        if parsed_start and (timestamp is None or timestamp < parsed_start):
            continue
        if parsed_end and (timestamp is None or timestamp > parsed_end):
            continue
        time_filtered.append(record)

    facet_filters = {
        "asset_paths": _normalize_list(asset_paths),
        "map_names": _normalize_list(map_names),
        "plugin_names": _normalize_list(plugin_names),
        "module_names": _normalize_list(module_names),
        "class_names": _normalize_list(class_names),
        "blueprint_paths": _normalize_list(blueprint_paths),
    }
    facet_filtered = [
        record
        for record in time_filtered
        if _matches_facets(record, system_area=system_area, facet_filters=facet_filters)
    ]

    recall: list[tuple[CompilableRecord, float]] = []
    for record in facet_filtered:
        match_score = _query_match_score(record, query)
        if match_score < 0:
            continue
        recall.append((record, match_score))

    usage_stats = load_usage_stats(config)
    reference_counts = build_reference_counts(records)
    now = datetime.now(timezone.utc)
    ranked: list[tuple[CompilableRecord, dict[str, Any], float]] = []
    for record, match_score in recall:
        record_id = str(record.metadata.get("id", ""))
        score_data = score_record(
            record.metadata,
            usage_entry=usage_stats.get(record_id, {}),
            reference_count=reference_counts.get(record_id, 0),
            now=now,
        )
        combined = float(score_data.get("total", 0.0)) + match_score
        ranked.append((record, score_data, combined))
    ranked.sort(key=lambda item: (-item[2], item[0].title.lower(), str(item[0].metadata.get("id", ""))))
    selected = [(record, score_data) for record, score_data, _combined in ranked[:limit]]

    core_constraints = [
        _summary(record, score_data, include_body=True)
        for record, score_data in selected
        if str(record.metadata.get("cognitive_level", "")) in {"dao", "fa"}
        or str(record.metadata.get("record_kind", "")) in {"decision", "system_rule"}
    ][:limit]
    relevant_rules = [
        _summary(record, score_data, include_body=True)
        for record, score_data in selected
        if str(record.metadata.get("record_kind", "")) in {"decision", "procedure", "system_rule"}
    ][:limit]
    key_evidence = [
        _summary(record, score_data, include_body=True)
        for record, score_data in selected
        if str(record.metadata.get("record_kind", "")) in {"observation", "incident", "note", "event"}
    ][:limit]
    selected_ids = {str(record.metadata.get("id", "")) for record, _score_data in selected}
    conflicts_result = memory_list_conflicts(config)
    open_conflicts = []
    if conflicts_result.get("ok"):
        for conflict in conflicts_result.get("conflicts", []):
            ids = {
                str(conflict.get("source", {}).get("id", "")),
                str(conflict.get("target", {}).get("id", "")),
            }
            if not selected_ids or selected_ids.intersection(ids):
                open_conflicts.append(conflict)

    return ok_result(
        "context retrieved",
        query=query,
        core_constraints=core_constraints,
        relevant_rules=relevant_rules,
        recent_snapshots=_recent_snapshots(config),
        key_evidence=key_evidence,
        open_conflicts=open_conflicts[:limit],
        next_steps=_next_steps(selected),
        selected_records=[_summary(record, score_data) for record, score_data in selected],
        pipeline={
            "scope_filter": len(scoped),
            "time_window_filter": len(time_filtered),
            "facet_filter": len(facet_filtered),
            "metadata_fts_recall": len(recall),
            "importance_rerank": len(ranked),
            "context_assembly": len(selected),
        },
        stats={
            **scan_stats,
            "returned_records": len(selected),
        },
    )
