from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .memory_budget import (  # P1-D: shared budget primitives
    IMPORTANT_MEMORY_DEFAULT_MAX_CHARS,
    IMPORTANT_MEMORY_DEFAULT_MAX_ITEMS,
    IMPORTANT_MEMORY_DEFAULT_MAX_TOKENS,
    IMPORTANT_MEMORY_FALLBACK_BODY,
    IMPORTANT_MEMORY_MIN_BODY_CHARS,
    fit_text_to_budget as _fit_text_to_budget,
    validate_budget_inputs as _validate_budget_inputs,
)
from .memory_compiler import load_compile_cache_entries
from .memory_config import MemoryConfig
from .memory_corpus import CompilableRecord, compact_body as _compact_body, iter_compilable_records as _iter_records
from .memory_lineage import memory_list_conflicts
from .memory_paths import PathSecurityError
from .memory_record_index import prefilter_record_paths
from .memory_result import error_result, ok_result
from .memory_scoring import build_reference_counts, load_usage_stats, parse_timestamp, score_record
from .token_estimator import estimate_tokens

DEFAULT_RETRIEVAL_SCOPES = ["shared", "personal", "session", "task_or_branch", "project_shared", "org_shared"]


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


def _parse_window(
    window_start: str | None,
    window_end: str | None,
) -> tuple[datetime | None, datetime | None] | dict[str, Any]:
    parsed_start = parse_timestamp(window_start) if window_start else None
    parsed_end = parse_timestamp(window_end) if window_end else None
    if window_start and parsed_start is None:
        return error_result("invalid_input", f"invalid window_start: {window_start}")
    if window_end and parsed_end is None:
        return error_result("invalid_input", f"invalid window_end: {window_end}")
    if parsed_start and parsed_end and parsed_start > parsed_end:
        return error_result("invalid_input", "window_start must be <= window_end")
    return parsed_start, parsed_end


def _selected_text(item: dict[str, Any]) -> str:
    return "\n".join(
        [
            item.get("title", ""),
            item.get("body", ""),
            " ".join(str(reason) for reason in item.get("reason_selected", []) if str(reason).strip()),
        ]
    ).strip()


def _selection_reasons(record: CompilableRecord, score_data: dict[str, Any], match_score: float) -> list[str]:
    metadata = record.metadata
    reasons: list[str] = []
    if match_score > 0:
        reasons.append("matched_query")
    kind = str(metadata.get("record_kind", "")).strip()
    if kind:
        reasons.append(f"kind:{kind}")
    level = str(metadata.get("cognitive_level", "")).strip()
    if level in {"dao", "fa", "shu"}:
        reasons.append(f"level:{level}")
    if float(score_data.get("total", 0.0)) >= 0.6:
        reasons.append("high_importance")
    if int(score_data.get("usage", {}).get("compile_hit_count", 0) or 0) > 0:
        reasons.append("recently_reused")
    return reasons[:4]


def _build_memory_item(
    record: CompilableRecord,
    score_data: dict[str, Any],
    *,
    match_score: float,
    body_text: str,
    rank: int,
    degraded: bool,
) -> dict[str, Any]:
    metadata = record.metadata
    body = body_text.strip()
    text_for_budget = "\n".join(part for part in (record.title, body) if part).strip()
    return {
        "id": str(metadata.get("id", "")),
        "title": record.title,
        "path": record.path,
        "record_kind": metadata.get("record_kind"),
        "scope": metadata.get("scope"),
        "status": metadata.get("status"),
        "cognitive_level": metadata.get("cognitive_level"),
        "memory_tier": score_data.get("effective_memory_tier"),
        "importance_score": score_data.get("total"),
        "system_area": metadata.get("system_area"),
        "body": body,
        "reason_selected": _selection_reasons(record, score_data, match_score),
        "source_refs": [str(item) for item in metadata.get("source_refs", []) if str(item).strip()],
        "related_artifact_ids": [
            str(item) for item in metadata.get("related_artifact_ids", []) if str(item).strip()
        ],
        "query_match_score": round(match_score, 4),
        "rank": rank,
        "degraded": degraded,
        "chars": len(text_for_budget),
        "tokens_est": estimate_tokens(text_for_budget),
    }


def _collect_records(
    config: MemoryConfig,
    *,
    user: str | None,
    task_id: str | None,
    branch: str | None,
    include_scopes: list[str] | None,
    include_statuses: list[str] | None,
    preferred_tags: list[str] | None,
    window_start: str | None,
    window_end: str | None,
    system_area: str | None,
    asset_paths: list[str] | None,
    map_names: list[str] | None,
    plugin_names: list[str] | None,
    module_names: list[str] | None,
    class_names: list[str] | None,
    blueprint_paths: list[str] | None,
) -> dict[str, Any]:
    window = _parse_window(window_start, window_end)
    if isinstance(window, dict):
        return window
    parsed_start, parsed_end = window

    scopes_list = [str(item) for item in (include_scopes or DEFAULT_RETRIEVAL_SCOPES)]
    statuses_list = [str(item) for item in (include_statuses or ["raw", "candidate", "validated", "published", "degraded"])]
    facet_filters = {
        "asset_paths": _normalize_list(asset_paths),
        "map_names": _normalize_list(map_names),
        "plugin_names": _normalize_list(plugin_names),
        "module_names": _normalize_list(module_names),
        "class_names": _normalize_list(class_names),
        "blueprint_paths": _normalize_list(blueprint_paths),
    }

    include_rel_paths: set[str] | None = None
    prefilter = prefilter_record_paths(
        config,
        include_scopes=scopes_list,
        include_statuses=statuses_list,
        user=user,
        task_id=task_id,
        branch=branch,
        system_area=system_area,
        facet_filters=facet_filters,
    )
    if prefilter.get("ok"):
        include_rel_paths = {str(path) for path in prefilter.get("paths", [])}
        prefilter_stats = {
            "enabled": True,
            "candidate_paths": len(include_rel_paths),
            **(prefilter.get("stats") or {}),
        }
    else:
        prefilter_stats = {
            "enabled": False,
            "fallback_reason": prefilter.get("error"),
            "message": prefilter.get("message"),
        }

    try:
        records, scan_stats = _iter_records(config, include_rel_paths=include_rel_paths)
    except (PathSecurityError, FileNotFoundError) as exc:
        return error_result("path_error", str(exc))

    scopes = set(scopes_list)
    statuses = set(statuses_list)
    tags = set(_normalize_list(preferred_tags))
    # Author isolation: both legacy `personal` and schema v2 `user_private`
    # are private-to-author scopes; cross-user reads must be rejected.
    private_scopes = {"personal", "user_private"}
    scoped = [
        record
        for record in records
        if str(record.metadata.get("scope", "")) in scopes
        and str(record.metadata.get("status", "")) in statuses
        and (not user or str(record.metadata.get("scope", "")) not in private_scopes or str(record.metadata.get("author", "")) == user)
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

    facet_filtered = [
        record
        for record in time_filtered
        if _matches_facets(record, system_area=system_area, facet_filters=facet_filters)
    ]
    return ok_result(
        "records collected",
        records=records,
        scan_stats=scan_stats,
        prefilter_stats=prefilter_stats,
        scoped=scoped,
        time_filtered=time_filtered,
        facet_filtered=facet_filtered,
    )


def _rank_records(
    config: MemoryConfig,
    *,
    records: list[CompilableRecord],
    corpus_records: list[CompilableRecord],
    query: str | None,
) -> list[tuple[CompilableRecord, dict[str, Any], float, float]]:
    recall: list[tuple[CompilableRecord, float]] = []
    for record in records:
        match_score = _query_match_score(record, query)
        if match_score < 0:
            continue
        recall.append((record, match_score))

    usage_stats = load_usage_stats(config)
    reference_counts = build_reference_counts(corpus_records)
    now = datetime.now(timezone.utc)
    ranked: list[tuple[CompilableRecord, dict[str, Any], float, float]] = []
    for record, match_score in recall:
        record_id = str(record.metadata.get("id", ""))
        score_data = score_record(
            record.metadata,
            usage_entry=usage_stats.get(record_id, {}),
            reference_count=reference_counts.get(record_id, 0),
            now=now,
        )
        combined = float(score_data.get("total", 0.0)) + match_score
        ranked.append((record, score_data, match_score, combined))
    ranked.sort(key=lambda item: (-item[3], item[0].title.lower(), str(item[0].metadata.get("id", ""))))
    return ranked


def _pack_ranked_records(
    ranked: list[tuple[CompilableRecord, dict[str, Any], float, float]],
    *,
    max_chars: int | None,
    max_tokens: int | None,
    max_items: int | None,
    default_items: int,
) -> tuple[
    list[tuple[CompilableRecord, dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    effective_max_items = max_items or default_items
    selected_pairs: list[tuple[CompilableRecord, dict[str, Any]]] = []
    important_memories: list[dict[str, Any]] = []
    dropped_candidates: list[dict[str, Any]] = []
    used_chars = 0
    used_tokens = 0

    def remember_drop(record: CompilableRecord, score_data: dict[str, Any], reason: str) -> None:
        if len(dropped_candidates) >= 10:
            return
        dropped_candidates.append(
            {
                "id": str(record.metadata.get("id", "")),
                "title": record.title,
                "path": record.path,
                "importance_score": score_data.get("total"),
                "drop_reason": reason,
            }
        )

    for rank, (record, score_data, match_score, _combined) in enumerate(ranked, start=1):
        if effective_max_items is not None and len(important_memories) >= effective_max_items:
            remember_drop(record, score_data, "max_items_reached")
            continue

        remaining_chars = None if max_chars is None else max_chars - used_chars
        remaining_tokens = None if max_tokens is None else max_tokens - used_tokens
        if remaining_chars is not None and remaining_chars <= 0:
            remember_drop(record, score_data, "max_chars_reached")
            continue
        if remaining_tokens is not None and remaining_tokens <= 0:
            remember_drop(record, score_data, "max_tokens_reached")
            continue

        body_text = _compact_body(record)
        fitted_body, degraded = _fit_text_to_budget(
            body_text,
            remaining_chars=remaining_chars,
            remaining_tokens=remaining_tokens,
        )
        if not fitted_body and remaining_chars != 0 and remaining_tokens != 0:
            fitted_body, degraded = _fit_text_to_budget(
                IMPORTANT_MEMORY_FALLBACK_BODY,
                remaining_chars=remaining_chars,
                remaining_tokens=remaining_tokens,
            )

        if not fitted_body:
            remember_drop(record, score_data, "budget_exhausted")
            continue
        if (
            len(fitted_body) < IMPORTANT_MEMORY_MIN_BODY_CHARS
            and len(body_text.strip()) >= IMPORTANT_MEMORY_MIN_BODY_CHARS
            and fitted_body != IMPORTANT_MEMORY_FALLBACK_BODY
        ):
            remember_drop(record, score_data, "insufficient_body_budget")
            continue

        item = _build_memory_item(
            record,
            score_data,
            match_score=match_score,
            body_text=fitted_body,
            rank=rank,
            degraded=degraded,
        )
        item_text = _selected_text(item)
        item_chars = len(item_text)
        item_tokens = estimate_tokens(item_text)
        if max_chars is not None and used_chars + item_chars > max_chars:
            remember_drop(record, score_data, "max_chars_reached")
            continue
        if max_tokens is not None and used_tokens + item_tokens > max_tokens:
            remember_drop(record, score_data, "max_tokens_reached")
            continue

        item["chars"] = item_chars
        item["tokens_est"] = item_tokens
        important_memories.append(item)
        selected_pairs.append((record, score_data))
        used_chars += item_chars
        used_tokens += item_tokens

    budget_report = {
        "max_chars": max_chars,
        "max_tokens": max_tokens,
        "max_items": effective_max_items,
        "used_chars": used_chars,
        "used_tokens_est": used_tokens,
        "used_items": len(important_memories),
        "dropped_candidates": len(dropped_candidates),
    }
    return selected_pairs, important_memories, dropped_candidates, budget_report


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


def _section_summary(record: CompilableRecord, score_data: dict[str, Any]) -> dict[str, Any]:
    result = _summary(record, score_data, include_body=False)
    result["context_item_id"] = str(record.metadata.get("id", ""))
    return result


def memory_get_important_memories(
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
    max_chars: int | None = None,
    max_tokens: int | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    budget_error = _validate_budget_inputs(max_chars=max_chars, max_tokens=max_tokens, max_items=max_items)
    if budget_error:
        return budget_error

    effective_max_chars = max_chars if max_chars is not None else IMPORTANT_MEMORY_DEFAULT_MAX_CHARS
    effective_max_tokens = max_tokens if max_tokens is not None else IMPORTANT_MEMORY_DEFAULT_MAX_TOKENS
    effective_max_items = max_items if max_items is not None else (top_k or IMPORTANT_MEMORY_DEFAULT_MAX_ITEMS)

    collected = _collect_records(
        config,
        user=user,
        task_id=task_id,
        branch=branch,
        include_scopes=include_scopes,
        include_statuses=include_statuses,
        preferred_tags=preferred_tags,
        window_start=window_start,
        window_end=window_end,
        system_area=system_area,
        asset_paths=asset_paths,
        map_names=map_names,
        plugin_names=plugin_names,
        module_names=module_names,
        class_names=class_names,
        blueprint_paths=blueprint_paths,
    )
    if not collected.get("ok"):
        return collected

    records = collected["records"]
    facet_filtered = collected["facet_filtered"]
    ranked = _rank_records(config, records=facet_filtered, corpus_records=records, query=query)
    selected_pairs, important_memories, dropped_candidates, budget_report = _pack_ranked_records(
        ranked,
        max_chars=effective_max_chars,
        max_tokens=effective_max_tokens,
        max_items=effective_max_items,
        default_items=IMPORTANT_MEMORY_DEFAULT_MAX_ITEMS,
    )

    evidence_refs = sorted(
        {
            ref
            for item in important_memories
            for ref in (
                # P1-E: aggregate every observable provenance signal so callers
                # can audit the digest without re-fetching each record.
                *(str(r).strip() for r in item.get("source_refs", [])),
                *(str(r).strip() for r in item.get("related_artifact_ids", [])),
                str(item.get("path") or "").strip(),
                str(item.get("id") or "").strip(),
            )
            if ref
        }
    )
    suggested_externalization = [
        {
            "id": item["id"],
            "title": item["title"],
            "importance_score": item["importance_score"],
            "reason": "stable_high_value_memory",
        }
        for item in important_memories
        if float(item.get("importance_score", 0.0) or 0.0) >= 0.6
        and str(item.get("status", "")) in {"validated", "published"}
    ]

    return ok_result(
        "important memories retrieved",
        query=query,
        important_memories=important_memories,
        evidence_refs=evidence_refs,
        suggested_externalization=suggested_externalization,
        dropped_candidates=dropped_candidates,
        budget_report=budget_report,
        selected_records=[_summary(record, score_data) for record, score_data in selected_pairs],
        pipeline={
            "scope_filter": len(collected["scoped"]),
            "time_window_filter": len(collected["time_filtered"]),
            "facet_filter": len(facet_filtered),
            "metadata_fts_recall": len(ranked),
            "importance_rerank": len(ranked),
            "budget_first_packing": len(important_memories),
        },
        stats={
            **collected["scan_stats"],
            "prefilter": collected["prefilter_stats"],
            "returned_records": len(important_memories),
        },
    )


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
    max_chars: int | None = None,
    max_tokens: int | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    limit = top_k or 10
    if limit <= 0:
        return error_result("invalid_input", "top_k must be >= 1")
    budget_error = _validate_budget_inputs(max_chars=max_chars, max_tokens=max_tokens, max_items=max_items)
    if budget_error:
        return budget_error

    collected = _collect_records(
        config,
        user=user,
        task_id=task_id,
        branch=branch,
        include_scopes=include_scopes,
        include_statuses=include_statuses,
        preferred_tags=preferred_tags,
        window_start=window_start,
        window_end=window_end,
        system_area=system_area,
        asset_paths=asset_paths,
        map_names=map_names,
        plugin_names=plugin_names,
        module_names=module_names,
        class_names=class_names,
        blueprint_paths=blueprint_paths,
    )
    if not collected.get("ok"):
        return collected

    records = collected["records"]
    facet_filtered = collected["facet_filtered"]
    ranked = _rank_records(config, records=facet_filtered, corpus_records=records, query=query)
    effective_max_chars = max_chars if max_chars is not None else IMPORTANT_MEMORY_DEFAULT_MAX_CHARS
    effective_max_tokens = max_tokens if max_tokens is not None else IMPORTANT_MEMORY_DEFAULT_MAX_TOKENS
    effective_max_items = max_items if max_items is not None else limit
    selected, context_items, dropped_candidates, budget_report = _pack_ranked_records(
        ranked,
        max_chars=effective_max_chars,
        max_tokens=effective_max_tokens,
        max_items=effective_max_items,
        default_items=limit,
    )

    core_constraints = [
        _section_summary(record, score_data)
        for record, score_data in selected
        if str(record.metadata.get("cognitive_level", "")) in {"dao", "fa"}
        or str(record.metadata.get("record_kind", "")) in {"decision", "system_rule"}
    ][:limit]
    relevant_rules = [
        _section_summary(record, score_data)
        for record, score_data in selected
        if str(record.metadata.get("record_kind", "")) in {"decision", "procedure", "system_rule"}
    ][:limit]
    key_evidence = [
        _section_summary(record, score_data)
        for record, score_data in selected
        if str(record.metadata.get("record_kind", "")) in {"observation", "incident", "note", "event"}
    ][:limit]
    selected_ids = {str(record.metadata.get("id", "")) for record, _score_data in selected}
    open_conflicts = []
    selected_have_conflicts = any(record.metadata.get("conflicts_with") for record, _score_data in selected)
    if selected_have_conflicts:
        conflicts_result = memory_list_conflicts(config)
        if conflicts_result.get("ok"):
            for conflict in conflicts_result.get("conflicts", []):
                ids = {
                    str(conflict.get("source", {}).get("id", "")),
                    str(conflict.get("target", {}).get("id", "")),
                }
                if not selected_ids or selected_ids.intersection(ids):
                    open_conflicts.append(conflict)
    evidence_refs = sorted(
        {
            ref
            for item in context_items
            for ref in (
                *(str(r).strip() for r in item.get("source_refs", [])),
                *(str(r).strip() for r in item.get("related_artifact_ids", [])),
                str(item.get("path") or "").strip(),
                str(item.get("id") or "").strip(),
            )
            if ref
        }
    )

    return ok_result(
        "context retrieved",
        query=query,
        context_items=context_items,
        core_constraints=core_constraints,
        relevant_rules=relevant_rules,
        recent_snapshots=_recent_snapshots(config),
        key_evidence=key_evidence,
        open_conflicts=open_conflicts[:limit],
        next_steps=_next_steps(selected),
        evidence_refs=evidence_refs,
        dropped_candidates=dropped_candidates,
        budget_report=budget_report,
        selected_records=[_summary(record, score_data) for record, score_data in selected],
        pipeline={
            "scope_filter": len(collected["scoped"]),
            "time_window_filter": len(collected["time_filtered"]),
            "facet_filter": len(facet_filtered),
            "metadata_fts_recall": len(ranked),
            "importance_rerank": len(ranked),
            "budget_first_packing": len(context_items),
            "context_assembly": len(selected),
        },
        stats={
            **collected["scan_stats"],
            "prefilter": collected["prefilter_stats"],
            "returned_records": len(context_items),
        },
    )
