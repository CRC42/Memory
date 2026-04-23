from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_config import DEFAULT_ALLOWED_TAGS, MemoryConfig
from .memory_events import append_event, get_current_user
from .memory_paths import PathManager, PathSecurityError
from .memory_result import error_result, ok_result

SCHEMA_VERSION = "1.0"
SCHEMA_VERSION_V2 = "2.0"

V1_RECORD_KINDS = {
    "note",
    "event",
    "claim_candidate",
    "rule_candidate",
    "handoff",
    "skill_candidate",
    "validation_result",
    "system_rule",
    "archive_record",
}

P3_RECORD_KINDS = {
    "observation",
    "artifact_ref",
    "incident",
    "decision",
    "procedure",
    "snapshot_daily",
    "snapshot_weekly",
    "snapshot_monthly",
}

ALLOWED_RECORD_KINDS = V1_RECORD_KINDS | P3_RECORD_KINDS

V1_SCOPES = {"personal", "shared", "local", "archive"}
P3_SCOPES = {"session", "user_private", "task_or_branch", "project_shared", "org_shared"}
ALLOWED_SCOPES = V1_SCOPES | P3_SCOPES
ALLOWED_STATUSES = {"raw", "candidate", "validated", "published", "degraded", "archived"}
ALLOWED_MEMORY_TIERS = {"hot", "warm", "cold", "fossil"}
ALLOWED_COGNITIVE_LEVELS = {"dao", "fa", "shu"}

V2_LIST_FIELDS = [
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

V2_SCALAR_FIELDS = [
    "occurred_at",
    "valid_from",
    "valid_to",
    "memory_tier",
    "cognitive_level",
    "importance_score",
    "system_area",
]

V2_FIELDS = set(V2_LIST_FIELDS) | set(V2_SCALAR_FIELDS)

# Built-in default tag vocabulary. Sourced from memory_config so the runtime
# validator and the default-config writer cannot drift apart. Custom configs
# can still override via tag_schema.allowed_tags.
ALLOWED_TAGS = frozenset(DEFAULT_ALLOWED_TAGS)

_SCALAR_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _parse_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if value in {"null", "None", "~"}:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    if _SCALAR_RE.match(value):
        if "." in value:
            return float(value)
        return int(value)
    return value


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if (
        not text
        or _SCALAR_RE.match(text)
        or text in {"null", "None", "~", "true", "false"}
        or any(char in text for char in [":", "#", "[", "]", "{", "}", ","])
        or text != text.strip()
    ):
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text


def parse_front_matter(front_matter: str) -> dict[str, Any]:
    """Parse the small YAML subset used by memory records."""
    parsed: dict[str, Any] = {}
    current_list_key: str | None = None

    for raw_line in front_matter.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError("list item found before a key")
            parsed.setdefault(current_list_key, []).append(_parse_scalar(stripped[2:]))
            continue
        if ":" not in stripped:
            raise ValueError(f"invalid front matter line: {raw_line}")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError("front matter key must not be empty")
        if raw_value.strip() == "":
            parsed[key] = []
            current_list_key = key
        else:
            parsed[key] = _parse_scalar(raw_value)
            current_list_key = None

    return parsed


def parse_record_markdown(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("record markdown must start with front matter")
    try:
        front_matter, body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError("record markdown front matter is not closed") from exc
    return parse_front_matter(front_matter), body.lstrip("\n")


def dump_front_matter(metadata: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in metadata.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_format_scalar(item)}")
        else:
            lines.append(f"{key}: {_format_scalar(value)}")
    return "\n".join(lines)


def render_record_markdown(metadata: dict[str, Any], body: str) -> str:
    return f"---\n{dump_front_matter(metadata)}\n---\n\n{body.strip()}\n"


def _default_status(record_kind: str) -> str:
    if record_kind.endswith("_candidate"):
        return "candidate"
    if record_kind == "system_rule":
        return "published"
    if record_kind == "archive_record":
        return "archived"
    return "raw"


def _record_id(now: datetime) -> str:
    return f"mem_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _slug_user(author: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", author.strip()).strip("-._")
    return slug or "unknown"


def _target_path(record_id: str, record_kind: str, scope: str, status: str, author: str) -> str:
    if status == "candidate":
        return f"memory-bank/candidates/{record_id}.md"
    if status == "archived" or record_kind == "archive_record" or scope == "archive":
        return f"memory-bank/archive/{record_id}.md"
    if scope in {"shared", "project_shared", "org_shared"} or status == "published" or record_kind == "system_rule":
        return f"memory-bank/shared/{record_id}.md"
    return f"memory-bank/people/{_slug_user(author)}/{record_id}.md"


def target_path_for_record(record_id: str, record_kind: str, scope: str, status: str, author: str) -> str:
    return _target_path(record_id, record_kind, scope, status, author)


def _validate_record_input(
    *,
    content_markdown: str,
    schema_version: str,
    record_kind: str,
    scope: str,
    status: str,
    tags: list[str],
    confidence: float | None,
    memory_tier: str | None = None,
    cognitive_level: str | None = None,
    importance_score: float | None = None,
    allowed_tags: list[str] | None = None,
) -> str | None:
    if not content_markdown.strip():
        return "content_markdown must not be empty"
    if schema_version not in {SCHEMA_VERSION, SCHEMA_VERSION_V2}:
        return f"schema_version must be one of: {SCHEMA_VERSION}, {SCHEMA_VERSION_V2}"
    if record_kind not in ALLOWED_RECORD_KINDS:
        return f"record_kind must be one of: {', '.join(sorted(ALLOWED_RECORD_KINDS))}"
    if scope not in ALLOWED_SCOPES:
        return f"scope must be one of: {', '.join(sorted(ALLOWED_SCOPES))}"
    if status not in ALLOWED_STATUSES:
        return f"status must be one of: {', '.join(sorted(ALLOWED_STATUSES))}"
    allowed_tag_set = set(allowed_tags or ALLOWED_TAGS)
    unknown_tags = sorted(set(tags) - allowed_tag_set)
    if unknown_tags:
        return f"tags contain unsupported value(s): {', '.join(unknown_tags)}"
    if confidence is not None and not 0 <= confidence <= 1:
        return "confidence must be between 0 and 1"
    if memory_tier is not None and memory_tier not in ALLOWED_MEMORY_TIERS:
        return f"memory_tier must be one of: {', '.join(sorted(ALLOWED_MEMORY_TIERS))}"
    if cognitive_level is not None and cognitive_level not in ALLOWED_COGNITIVE_LEVELS:
        return f"cognitive_level must be one of: {', '.join(sorted(ALLOWED_COGNITIVE_LEVELS))}"
    if importance_score is not None and not 0 <= importance_score <= 1:
        return "importance_score must be between 0 and 1"
    return None


def _normalize_string_list(value: list[str] | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _uses_v2_schema(record_kind: str, scope: str, metadata: dict[str, Any]) -> bool:
    if record_kind in P3_RECORD_KINDS or scope in P3_SCOPES:
        return True
    return any(metadata.get(field) not in (None, [], "") for field in V2_FIELDS)


def memory_write_record(
    config: MemoryConfig,
    *,
    content_markdown: str,
    schema_version: str | None = None,
    record_kind: str = "note",
    scope: str = "personal",
    status: str | None = None,
    author: str | None = None,
    tags: list[str] | None = None,
    confidence: float | None = None,
    source_refs: list[str] | None = None,
    task_id: str | None = None,
    branch: str | None = None,
    validated_by: str | None = None,
    classifier_model: str | None = None,
    classifier_prompt_version: str | None = None,
    tag_schema_version: str | None = None,
    occurred_at: str | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    memory_tier: str | None = None,
    cognitive_level: str | None = None,
    derived_from_record_ids: list[str] | None = None,
    derived_from_snapshot_ids: list[str] | None = None,
    derived_from_revision_ids: list[str] | None = None,
    supersedes: list[str] | None = None,
    conflicts_with: list[str] | None = None,
    related_artifact_ids: list[str] | None = None,
    importance_score: float | None = None,
    asset_paths: list[str] | None = None,
    map_names: list[str] | None = None,
    plugin_names: list[str] | None = None,
    module_names: list[str] | None = None,
    class_names: list[str] | None = None,
    blueprint_paths: list[str] | None = None,
    system_area: str | None = None,
) -> dict[str, Any]:
    """Write a structured memory record as Markdown + Front Matter."""
    normalized_tags = _normalize_string_list(tags)
    normalized_source_refs = _normalize_string_list(source_refs)
    effective_status = status or _default_status(record_kind)
    effective_author = (author or get_current_user(config.repo_root)).strip() or "unknown"
    effective_confidence = float(confidence) if confidence is not None else None
    effective_importance_score = float(importance_score) if importance_score is not None else None
    v2_metadata: dict[str, Any] = {
        "occurred_at": occurred_at,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "memory_tier": memory_tier,
        "cognitive_level": cognitive_level,
        "derived_from_record_ids": _normalize_string_list(derived_from_record_ids),
        "derived_from_snapshot_ids": _normalize_string_list(derived_from_snapshot_ids),
        "derived_from_revision_ids": _normalize_string_list(derived_from_revision_ids),
        "supersedes": _normalize_string_list(supersedes),
        "conflicts_with": _normalize_string_list(conflicts_with),
        "related_artifact_ids": _normalize_string_list(related_artifact_ids),
        "importance_score": effective_importance_score,
        "asset_paths": _normalize_string_list(asset_paths),
        "map_names": _normalize_string_list(map_names),
        "plugin_names": _normalize_string_list(plugin_names),
        "module_names": _normalize_string_list(module_names),
        "class_names": _normalize_string_list(class_names),
        "blueprint_paths": _normalize_string_list(blueprint_paths),
        "system_area": system_area,
    }
    effective_schema_version = (
        schema_version
        or (SCHEMA_VERSION_V2 if _uses_v2_schema(record_kind, scope, v2_metadata) else SCHEMA_VERSION)
    )
    if effective_schema_version == SCHEMA_VERSION and _uses_v2_schema(record_kind, scope, v2_metadata):
        return error_result("invalid_input", "schema_version 2.0 is required for P3 record kinds, scopes, or v2 fields")

    validation_error = _validate_record_input(
        content_markdown=content_markdown,
        schema_version=effective_schema_version,
        record_kind=record_kind,
        scope=scope,
        status=effective_status,
        tags=normalized_tags,
        confidence=effective_confidence,
        memory_tier=memory_tier,
        cognitive_level=cognitive_level,
        importance_score=effective_importance_score,
        allowed_tags=config.tag_allowed_tags,
    )
    if validation_error:
        return error_result("invalid_input", validation_error)

    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    record_id = _record_id(now)
    rel_path = _target_path(record_id, record_kind, scope, effective_status, effective_author)

    metadata: dict[str, Any] = {
        "schema_version": effective_schema_version,
        "id": record_id,
        "record_kind": record_kind,
        "scope": scope,
        "status": effective_status,
        "author": effective_author,
        "created_at": now_text,
        "updated_at": now_text,
        "tags": normalized_tags,
        "confidence": effective_confidence,
        "source_refs": normalized_source_refs,
        "task_id": task_id,
        "branch": branch,
        "validated_by": validated_by,
        "last_used_at": None,
        "classifier_model": classifier_model,
        "classifier_prompt_version": classifier_prompt_version,
        "tag_schema_version": tag_schema_version or config.tag_schema_version,
    }
    if effective_schema_version == SCHEMA_VERSION_V2:
        metadata.update(v2_metadata)

    final_content = render_record_markdown(metadata, content_markdown)

    manager = PathManager(config)
    try:
        resolved = manager.resolve(rel_path, must_exist=False, must_be_file=False)
    except PathSecurityError as exc:
        return error_result("path_not_allowed", str(exc))

    if resolved.exists():
        return error_result("already_exists", f"record already exists: {rel_path}")
    if resolved.exists() and not resolved.is_file():
        return error_result("invalid_path", f"target is not a file: {rel_path}")

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        # Atomic create: O_CREAT|O_EXCL guarantees we are the unique writer
        # for this record id (closes the TOCTOU window above) and prevents
        # concurrent MCP clients from clobbering each other's records.
        fd = os.open(str(resolved), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(final_content)
        except Exception:
            # If write fails after creation, clean up the empty file so retries can succeed.
            try:
                resolved.unlink()
            except OSError:
                pass
            raise
    except FileExistsError:
        return error_result("already_exists", f"record already exists: {rel_path}")
    except OSError as exc:
        return error_result("write_failed", f"failed to write record: {exc}")

    append_event(
        config,
        "memory_write_record",
        {
            "id": record_id,
            "path": rel_path,
            "record_kind": record_kind,
            "scope": scope,
            "status": effective_status,
            "tags": normalized_tags,
            "task_id": task_id,
            "branch": branch,
        },
    )

    return ok_result(
        "record written",
        id=record_id,
        path=rel_path,
        record_kind=record_kind,
        scope=scope,
        status=effective_status,
    )
