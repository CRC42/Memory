from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .memory_config import MemoryConfig
from .memory_corpus import CompilableRecord
from .memory_scoring import build_reference_counts, load_usage_stats, parse_timestamp, score_record


def record_time(record: CompilableRecord) -> datetime | None:
    metadata = record.metadata
    for key in ("occurred_at", "valid_from", "updated_at", "created_at"):
        parsed = parse_timestamp(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def record_sort_key(record: CompilableRecord) -> tuple[int, str, str]:
    status = str(record.metadata.get("status", ""))
    scope = str(record.metadata.get("scope", ""))
    status_rank = {"published": 0, "validated": 1, "candidate": 2, "raw": 3, "archived": 4}.get(status, 9)
    scope_rank = {"shared": 0, "personal": 1, "archive": 2, "local": 3}.get(scope, 9)
    return status_rank, f"{scope_rank}:{record.title.lower()}", str(record.metadata.get("id", ""))


def record_sort_with_score(record: CompilableRecord, score_data: dict[str, Any]) -> tuple[float, float, str]:
    timestamp = record_time(record)
    epoch = timestamp.timestamp() if timestamp is not None else 0.0
    return (-float(score_data.get("total", 0.0)), -epoch, record.title.lower())


def scored_records(config: MemoryConfig, records: list[CompilableRecord]) -> list[tuple[CompilableRecord, dict[str, Any]]]:
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
    scored.sort(key=lambda item: record_sort_with_score(item[0], item[1]))
    return scored


def summary_with_score(record: CompilableRecord, score_data: dict[str, Any]) -> dict[str, Any]:
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
