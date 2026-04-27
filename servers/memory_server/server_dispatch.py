"""Dispatch layer for the memory MCP server.

Extracted from `server.py` (P1-A). Maps `(name, args)` to the right
domain function and returns a result dict. No FastMCP / asyncio entry
points live here.
"""

from __future__ import annotations

import logging
from typing import Any

from .memory_backup import backup_files
from .memory_compactor import compact_memory
from .memory_compiler import memory_compare_snapshots, memory_compile, memory_get_runtime_digest
from .memory_config import MemoryConfig
from .memory_governance import memory_archive_record, memory_publish_candidate, memory_validate_candidate
from .memory_guard import memory_guard_check
from .memory_key_documents import KEY_DOCUMENT_KEYS, rebuild_key_documents
from .memory_lineage import (
    memory_link_artifact,
    memory_list_conflicts,
    memory_record_observation,
    memory_trace_lineage,
)
from .memory_maintenance import memory_delete_record, memory_health_check, memory_migrate_records
from .memory_reader import memory_get
from .memory_record_index import memory_rebuild_index, memory_search_records, memory_update_index
from .memory_records import memory_write_record
from .memory_result import error_result
from .memory_retrieval import memory_get_important_memories, memory_retrieve_context
from .memory_search import memory_search
from .memory_writer import memory_write as memory_write_file

logger = logging.getLogger(__name__)


def _build_llm_client(plugin_root=None):
    """Lazily build an LLMClient. Returns (client, error_dict).

    Returns ``(None, error_dict)`` if config is unavailable so callers can
    surface ``llm_unavailable`` without crashing the primary write/read
    path. Importing here keeps `memory_llm` optional at module load time.
    """
    try:
        from .memory_llm import LLMClient, LLMConfigError  # local import: optional dep
    except Exception as exc:  # pragma: no cover — defensive
        return None, error_result("llm_unavailable", f"memory_llm import failed: {exc}")
    try:
        return LLMClient(plugin_root=plugin_root), None
    except LLMConfigError as exc:
        return None, error_result("llm_unavailable", str(exc))
    except Exception as exc:  # pragma: no cover — defensive
        return None, error_result("llm_unavailable", f"failed to build LLMClient: {exc}")


def _run_distill_for_write(
    config: MemoryConfig,
    args: dict[str, Any],
    write_result: dict[str, Any],
) -> dict[str, Any]:
    """Distill the just-written record and persist the summary as a 2nd record.

    Failure modes are reported in-band (``{ok: False, error, message}``)
    so the primary write result is never lost. Always opt-in via
    ``distill=True``.
    """
    from datetime import datetime, timezone

    raw_id = str(write_result.get("id") or "").strip()
    if not raw_id:
        return error_result("distill_skipped", "raw record missing id; cannot distill")
    raw_path = str(write_result.get("path") or "").strip()
    raw_content = str(args.get("content_markdown") or "")
    if not raw_content.strip():
        return error_result("distill_skipped", "empty raw content; nothing to distill")

    client, err = _build_llm_client()
    if err is not None:
        return err
    try:
        from .memory_llm import LLMError, make_raw_record
        from .memory_llm_pipeline import map_reduce_distill
    except Exception as exc:  # pragma: no cover — defensive
        return error_result("llm_unavailable", f"pipeline import failed: {exc}")

    captured_at = datetime.now(timezone.utc).isoformat()
    raw_view = make_raw_record(
        record_id=raw_id,
        content=raw_content,
        source=f"memory_write:{raw_path}" if raw_path else "memory_write",
        captured_at=captured_at,
        author=str(args.get("author") or "system"),
    )

    try:
        distilled = map_reduce_distill(
            client,
            [raw_view],
            record_id=f"{raw_id}-distilled",
            distilled_at=captured_at,
            user_instruction=args.get("distill_user_instruction"),
            kind="distilled_summary",
            tags=args.get("distill_tags") or args.get("tags"),
            max_tokens=args.get("distill_max_tokens"),
        )
    except LLMError as exc:
        return error_result("distill_failed", str(exc))
    except Exception as exc:  # pragma: no cover — unexpected
        logger.exception("unexpected error in distill pipeline")
        return error_result("distill_failed", f"unexpected: {exc}")

    summary_text = str(distilled.get("content") or "").strip()
    if not summary_text:
        return error_result("distill_failed", "empty summary text")

    persist = memory_write_record(
        config,
        content_markdown=summary_text,
        record_kind="distilled_summary",
        scope="user_private",
        status="distilled",
        author=str(args.get("author") or "system"),
        derived_from_record_ids=[raw_id],
        provenance=str(distilled.get("provenance") or "llm"),
        immutable=bool(distilled.get("immutable", False)),
        authoritative=bool(distilled.get("authoritative", False)),
        replaceable=bool(distilled.get("replaceable", True)),
        model=str(distilled.get("model") or ""),
        distilled_at=str(distilled.get("distilled_at") or captured_at),
        task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
        branch=str(args["branch"]) if args.get("branch") is not None else None,
    )
    return {
        "ok": bool(persist.get("ok")),
        "summary": summary_text,
        "distilled_record_id": persist.get("id"),
        "distilled_path": persist.get("path"),
        "model": distilled.get("model"),
        "pipeline": distilled.get("pipeline", {}),
        "usage": client.usage_snapshot(),
        "persist_result": persist,
    }


def _run_recall_summarize(args: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """LLM map-reduce summary over already-retrieved records (read-only)."""
    if not records:
        return error_result("summarize_skipped", "no records to summarize")
    client, err = _build_llm_client()
    if err is not None:
        return err
    try:
        from .memory_llm import LLMError
        from .memory_llm_pipeline import summarize_records_for_recall
    except Exception as exc:  # pragma: no cover
        return error_result("llm_unavailable", f"pipeline import failed: {exc}")
    try:
        outcome = summarize_records_for_recall(
            client,
            records,
            query=args.get("summary_query") or args.get("query"),
            max_tokens=args.get("summary_max_tokens"),
            max_chars_per_record=int(args.get("summary_max_chars_per_record") or 4000),
        )
    except LLMError as exc:
        return error_result("summarize_failed", str(exc))
    except Exception as exc:  # pragma: no cover
        logger.exception("unexpected error in recall summarize")
        return error_result("summarize_failed", f"unexpected: {exc}")
    outcome["ok"] = True
    outcome["usage"] = client.usage_snapshot()
    return outcome


def _check_required(args: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    """Return error_result if any required key is missing from args, else None.

    Uses `k not in args or args[k] is None` to allow empty strings and zero values.
    """
    missing = [k for k in keys if k not in args or args[k] is None]
    if missing:
        return error_result("invalid_input", f"missing required parameter(s): {', '.join(missing)}")
    return None


def _dispatch_memory_read(config: MemoryConfig, args: dict[str, Any]) -> dict[str, Any]:
    operation = str(args.get("operation") or ("search" if args.get("query") else "get"))
    if operation == "get":
        err = _check_required(args, "path")
        if err:
            return err
        return memory_get(
            config,
            path=str(args.get("path", "")),
            start_line=args.get("start_line"),
            end_line=args.get("end_line"),
            max_chars=args.get("max_chars"),
        )
    if operation == "search":
        err = _check_required(args, "query")
        if err:
            return err
        return memory_search(
            config,
            query=str(args.get("query", "")),
            scopes=args.get("scopes"),
            top_k=args.get("top_k"),
            include_paths=args.get("include_paths"),
            exclude_paths=args.get("exclude_paths"),
        )
    if operation == "search_records":
        err = _check_required(args, "query")
        if err:
            return err
        return memory_search_records(config, query=str(args.get("query", "")), top_k=args.get("top_k"))
    if operation == "runtime_digest":
        return memory_get_runtime_digest(
            config,
            user=str(args["user"]) if args.get("user") is not None else None,
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            max_chars=args.get("max_chars"),
        )
    return error_result("invalid_input", "operation must be one of: get, search, search_records, runtime_digest")


def _dispatch_memory_write(config: MemoryConfig, args: dict[str, Any]) -> dict[str, Any]:
    if args.get("operation") is not None:
        operation = str(args.get("operation"))
    elif args.get("content_markdown") is not None:
        operation = "record"
    elif args.get("record_id") is not None:
        operation = "link_artifact"
    else:
        operation = "file"

    if operation == "file":
        err = _check_required(args, "path", "content")
        if err:
            return err
        return memory_write_file(
            config,
            path=str(args.get("path", "")),
            content=str(args.get("content", "")),
            mode=str(args.get("mode", "overwrite")),
            backup=bool(args.get("backup", True)),
            create_if_missing=bool(args.get("create_if_missing", True)),
            reason=args.get("reason"),
            inject_user_tag=args.get("inject_user_tag"),
            if_match=args.get("if_match"),
            request_id=args.get("request_id"),
        )
    if operation == "record":
        err = _check_required(args, "content_markdown")
        if err:
            return err
        write_result = memory_write_record(
            config,
            content_markdown=str(args.get("content_markdown", "")),
            schema_version=str(args["schema_version"]) if args.get("schema_version") is not None else None,
            record_kind=str(args.get("record_kind", "note")),
            scope=str(args.get("scope", "personal")),
            status=str(args["status"]) if args.get("status") is not None else None,
            author=str(args["author"]) if args.get("author") is not None else None,
            tags=args.get("tags"),
            confidence=args.get("confidence"),
            source_refs=args.get("source_refs"),
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            validated_by=str(args["validated_by"]) if args.get("validated_by") is not None else None,
            classifier_model=str(args["classifier_model"]) if args.get("classifier_model") is not None else None,
            classifier_prompt_version=(
                str(args["classifier_prompt_version"])
                if args.get("classifier_prompt_version") is not None
                else None
            ),
            tag_schema_version=str(args.get("tag_schema_version", "v1")),
            occurred_at=str(args["occurred_at"]) if args.get("occurred_at") is not None else None,
            valid_from=str(args["valid_from"]) if args.get("valid_from") is not None else None,
            valid_to=str(args["valid_to"]) if args.get("valid_to") is not None else None,
            memory_tier=str(args["memory_tier"]) if args.get("memory_tier") is not None else None,
            cognitive_level=str(args["cognitive_level"]) if args.get("cognitive_level") is not None else None,
            derived_from_record_ids=args.get("derived_from_record_ids"),
            derived_from_snapshot_ids=args.get("derived_from_snapshot_ids"),
            derived_from_revision_ids=args.get("derived_from_revision_ids"),
            supersedes=args.get("supersedes"),
            conflicts_with=args.get("conflicts_with"),
            related_artifact_ids=args.get("related_artifact_ids"),
            importance_score=args.get("importance_score"),
            asset_paths=args.get("asset_paths"),
            map_names=args.get("map_names"),
            plugin_names=args.get("plugin_names"),
            module_names=args.get("module_names"),
            class_names=args.get("class_names"),
            blueprint_paths=args.get("blueprint_paths"),
            system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
        )
        if bool(args.get("distill")) and write_result.get("ok"):
            distill_outcome = _run_distill_for_write(config, args, write_result)
            write_result["distilled"] = distill_outcome
        return write_result
    if operation == "observation":
        err = _check_required(args, "content_markdown")
        if err:
            return err
        return memory_record_observation(
            config,
            content_markdown=str(args.get("content_markdown", "")),
            author=str(args["author"]) if args.get("author") is not None else None,
            tags=args.get("tags"),
            confidence=args.get("confidence"),
            source_refs=args.get("source_refs"),
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            occurred_at=str(args["occurred_at"]) if args.get("occurred_at") is not None else None,
            memory_tier=str(args["memory_tier"]) if args.get("memory_tier") is not None else "hot",
            cognitive_level=str(args["cognitive_level"]) if args.get("cognitive_level") is not None else "shu",
            related_artifact_ids=args.get("related_artifact_ids"),
            asset_paths=args.get("asset_paths"),
            map_names=args.get("map_names"),
            plugin_names=args.get("plugin_names"),
            module_names=args.get("module_names"),
            class_names=args.get("class_names"),
            blueprint_paths=args.get("blueprint_paths"),
            system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
        )
    if operation == "link_artifact":
        err = _check_required(args, "record_id")
        if err:
            return err
        return memory_link_artifact(
            config,
            str(args.get("record_id", "")),
            related_artifact_ids=args.get("related_artifact_ids"),
            asset_paths=args.get("asset_paths"),
            map_names=args.get("map_names"),
            plugin_names=args.get("plugin_names"),
            module_names=args.get("module_names"),
            class_names=args.get("class_names"),
            blueprint_paths=args.get("blueprint_paths"),
            system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
        )
    return error_result("invalid_input", "operation must be one of: file, record, observation, link_artifact")


def _dispatch_memory_context(config: MemoryConfig, args: dict[str, Any]) -> dict[str, Any]:
    operation = str(args.get("operation") or "compile")
    if operation == "compile":
        return memory_compile(
            config,
            target=str(args.get("target", "runtime_digest")),
            user=str(args["user"]) if args.get("user") is not None else None,
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            include_scopes=args.get("include_scopes"),
            include_statuses=args.get("include_statuses"),
            preferred_tags=args.get("preferred_tags"),
            body_mode=str(args["body_mode"]) if args.get("body_mode") is not None else None,
            as_of=str(args["as_of"]) if args.get("as_of") is not None else None,
        )
    if operation == "runtime_digest":
        return memory_get_runtime_digest(
            config,
            user=str(args["user"]) if args.get("user") is not None else None,
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            max_chars=args.get("max_chars"),
        )
    if operation == "trace_lineage":
        err = _check_required(args, "record_id")
        if err:
            return err
        return memory_trace_lineage(config, str(args.get("record_id", "")), max_depth=args.get("max_depth"))
    if operation == "list_conflicts":
        return memory_list_conflicts(config, include_resolved=bool(args.get("include_resolved", False)))
    if operation == "compare_snapshots":
        err = _check_required(args, "path", "other_path")
        if err:
            return err
        return memory_compare_snapshots(
            config,
            path=str(args.get("path", "")),
            other_path=str(args.get("other_path", "")),
        )
    if operation == "retrieve_context":
        result = memory_retrieve_context(
            config,
            query=str(args["query"]) if args.get("query") is not None else None,
            user=str(args["user"]) if args.get("user") is not None else None,
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            include_scopes=args.get("include_scopes"),
            include_statuses=args.get("include_statuses"),
            preferred_tags=args.get("preferred_tags"),
            window_start=str(args["window_start"]) if args.get("window_start") is not None else None,
            window_end=str(args["window_end"]) if args.get("window_end") is not None else None,
            system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
            asset_paths=args.get("asset_paths"),
            map_names=args.get("map_names"),
            plugin_names=args.get("plugin_names"),
            module_names=args.get("module_names"),
            class_names=args.get("class_names"),
            blueprint_paths=args.get("blueprint_paths"),
            top_k=args.get("top_k"),
            max_chars=args.get("max_chars"),
            max_tokens=args.get("max_tokens"),
            max_items=args.get("max_items"),
        )
        if bool(args.get("summarize")) and result.get("ok"):
            summary_outcome = _run_recall_summarize(
                args,
                result.get("context_items") or result.get("selected_records") or [],
            )
            result["summary"] = summary_outcome
        return result
    if operation == "important_memories":
        return memory_get_important_memories(
            config,
            query=str(args["query"]) if args.get("query") is not None else None,
            user=str(args["user"]) if args.get("user") is not None else None,
            task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
            branch=str(args["branch"]) if args.get("branch") is not None else None,
            include_scopes=args.get("include_scopes"),
            include_statuses=args.get("include_statuses"),
            preferred_tags=args.get("preferred_tags"),
            window_start=str(args["window_start"]) if args.get("window_start") is not None else None,
            window_end=str(args["window_end"]) if args.get("window_end") is not None else None,
            system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
            asset_paths=args.get("asset_paths"),
            map_names=args.get("map_names"),
            plugin_names=args.get("plugin_names"),
            module_names=args.get("module_names"),
            class_names=args.get("class_names"),
            blueprint_paths=args.get("blueprint_paths"),
            top_k=args.get("top_k"),
            max_chars=args.get("max_chars"),
            max_tokens=args.get("max_tokens"),
            max_items=args.get("max_items"),
        )
    if operation == "config_diagnose":
        from .memory_diagnose import config_diagnose
        return config_diagnose(config)
    if operation == "rebuild_key_documents":
        raw_targets = args.get("targets")
        if raw_targets is not None and not isinstance(raw_targets, list):
            return error_result("invalid_input", "targets must be a list of key document names")
        return rebuild_key_documents(
            config,
            targets=[str(t) for t in raw_targets] if raw_targets else None,
            user=str(args["user"]) if args.get("user") is not None else None,
            renderer=str(args.get("renderer") or "deterministic"),
        )
    return error_result(
        "invalid_input",
        "operation must be one of: compile, runtime_digest, trace_lineage, list_conflicts, compare_snapshots, retrieve_context, important_memories, config_diagnose, rebuild_key_documents",
    )


# ── memory_enhance dispatch (LLM-backed soft enhancements, opt-in) ─────────


_ENHANCE_OPS = {
    "classify_record",
    "extract_candidates",
    "merge_candidates",
    "generate_skill_candidate",
    "explain_conflict",
    "generate_handoff",
}


def _dispatch_memory_enhance(config: MemoryConfig, args: dict[str, Any]) -> dict[str, Any]:
    op = str(args.get("operation") or "").strip()
    if op not in _ENHANCE_OPS:
        return error_result(
            "invalid_input",
            f"operation must be one of: {', '.join(sorted(_ENHANCE_OPS))}",
        )

    plugin_root = getattr(config, "plugin_root", None)
    client, err = _build_llm_client(plugin_root)
    if err is not None:
        return err

    # Local import to keep top-of-file lean.
    from . import memory_llm_enhance as enh
    from .memory_records import ALLOWED_RECORD_KINDS, ALLOWED_SCOPES, ALLOWED_TAGS

    try:
        if op == "classify_record":
            content = str(args.get("content_markdown") or args.get("content") or "")
            allowed_kinds = args.get("allowed_kinds") or sorted(ALLOWED_RECORD_KINDS)
            allowed_scopes = args.get("allowed_scopes") or sorted(ALLOWED_SCOPES)
            allowed_tags = args.get("allowed_tags") or sorted(ALLOWED_TAGS)
            return enh.classify_record(
                client,
                content=content,
                allowed_kinds=list(allowed_kinds),
                allowed_scopes=list(allowed_scopes),
                allowed_tags=list(allowed_tags),
                max_tokens=args.get("max_tokens"),
                thinking=args.get("thinking"),
                reasoning_effort=args.get("reasoning_effort"),
            )
        if op == "extract_candidates":
            return enh.extract_candidates(
                client,
                content=str(args.get("content_markdown") or args.get("content") or ""),
                source_record_id=(str(args["source_record_id"]) if args.get("source_record_id") else None),
                max_tokens=args.get("max_tokens"),
                thinking=args.get("thinking"),
                reasoning_effort=args.get("reasoning_effort"),
            )
        if op == "merge_candidates":
            return enh.merge_candidates(
                client,
                candidates=list(args.get("candidates") or []),
                max_tokens=args.get("max_tokens"),
                thinking=args.get("thinking"),
                reasoning_effort=args.get("reasoning_effort"),
            )
        if op == "generate_skill_candidate":
            return enh.generate_skill_candidate(
                client,
                records=list(args.get("records") or []),
                max_tokens=args.get("max_tokens"),
                thinking=args.get("thinking"),
                reasoning_effort=args.get("reasoning_effort"),
                max_chars_per_record=int(args.get("max_chars_per_record") or 4000),
            )
        if op == "explain_conflict":
            return enh.explain_conflict(
                client,
                record_a=dict(args.get("record_a") or {}),
                record_b=dict(args.get("record_b") or {}),
                max_tokens=args.get("max_tokens"),
                thinking=args.get("thinking"),
                reasoning_effort=args.get("reasoning_effort"),
            )
        if op == "generate_handoff":
            return enh.generate_handoff(
                client,
                records=list(args.get("records") or []),
                task_id=(str(args["task_id"]) if args.get("task_id") else None),
                branch=(str(args["branch"]) if args.get("branch") else None),
                max_tokens=args.get("max_tokens"),
                thinking=args.get("thinking"),
                reasoning_effort=args.get("reasoning_effort"),
                max_chars_per_record=int(args.get("max_chars_per_record") or 4000),
            )
    except Exception as exc:  # noqa: BLE001 — surface as in-band structured error
        return error_result(f"enhance_failed:{op}", str(exc))
    return error_result("invalid_input", f"unhandled enhance operation: {op}")


def _dispatch_tool(config: MemoryConfig, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call and return the result dict."""
    try:
        if name == "memory_read":
            return _dispatch_memory_read(config, args)
        elif name == "memory_context":
            return _dispatch_memory_context(config, args)
        elif name == "memory_get":
            err = _check_required(args, "path")
            if err:
                return err
            return memory_get(
                config,
                path=str(args.get("path", "")),
                start_line=args.get("start_line"),
                end_line=args.get("end_line"),
                max_chars=args.get("max_chars"),
            )
        elif name == "memory_search":
            err = _check_required(args, "query")
            if err:
                return err
            return memory_search(
                config,
                query=str(args.get("query", "")),
                scopes=args.get("scopes"),
                top_k=args.get("top_k"),
                include_paths=args.get("include_paths"),
                exclude_paths=args.get("exclude_paths"),
            )
        elif name == "memory_guard_check":
            return memory_guard_check(config)
        elif name == "memory_backup":
            return backup_files(
                config,
                paths=args.get("paths", []),
                reason=args.get("reason"),
                tag=args.get("tag"),
            )
        elif name == "memory_compact":
            err = _check_required(args, "path", "policy")
            if err:
                return err
            return compact_memory(
                config,
                path=str(args.get("path", "")),
                policy=str(args.get("policy", "")),
                dry_run=bool(args.get("dry_run", True)),
                backup=bool(args.get("backup", True)),
                archive_original=bool(args.get("archive_original", True)),
                compress_to_tokens=args.get("compress_to_tokens"),
            )
        elif name == "memory_write":
            return _dispatch_memory_write(config, args)
        elif name == "memory_enhance":
            return _dispatch_memory_enhance(config, args)
        elif name == "memory_write_record":
            err = _check_required(args, "content_markdown")
            if err:
                return err
            return memory_write_record(
                config,
                content_markdown=str(args.get("content_markdown", "")),
                schema_version=str(args["schema_version"]) if args.get("schema_version") is not None else None,
                record_kind=str(args.get("record_kind", "note")),
                scope=str(args.get("scope", "personal")),
                status=str(args["status"]) if args.get("status") is not None else None,
                author=str(args["author"]) if args.get("author") is not None else None,
                tags=args.get("tags"),
                confidence=args.get("confidence"),
                source_refs=args.get("source_refs"),
                task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
                branch=str(args["branch"]) if args.get("branch") is not None else None,
                validated_by=str(args["validated_by"]) if args.get("validated_by") is not None else None,
                classifier_model=str(args["classifier_model"]) if args.get("classifier_model") is not None else None,
                classifier_prompt_version=(
                    str(args["classifier_prompt_version"])
                    if args.get("classifier_prompt_version") is not None
                    else None
                ),
                tag_schema_version=str(args.get("tag_schema_version", "v1")),
                occurred_at=str(args["occurred_at"]) if args.get("occurred_at") is not None else None,
                valid_from=str(args["valid_from"]) if args.get("valid_from") is not None else None,
                valid_to=str(args["valid_to"]) if args.get("valid_to") is not None else None,
                memory_tier=str(args["memory_tier"]) if args.get("memory_tier") is not None else None,
                cognitive_level=str(args["cognitive_level"]) if args.get("cognitive_level") is not None else None,
                derived_from_record_ids=args.get("derived_from_record_ids"),
                derived_from_snapshot_ids=args.get("derived_from_snapshot_ids"),
                derived_from_revision_ids=args.get("derived_from_revision_ids"),
                supersedes=args.get("supersedes"),
                conflicts_with=args.get("conflicts_with"),
                related_artifact_ids=args.get("related_artifact_ids"),
                importance_score=args.get("importance_score"),
                asset_paths=args.get("asset_paths"),
                map_names=args.get("map_names"),
                plugin_names=args.get("plugin_names"),
                module_names=args.get("module_names"),
                class_names=args.get("class_names"),
                blueprint_paths=args.get("blueprint_paths"),
                system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
            )
        elif name == "memory_rebuild_index":
            return memory_rebuild_index(config)
        elif name == "memory_search_records":
            err = _check_required(args, "query")
            if err:
                return err
            return memory_search_records(
                config,
                query=str(args.get("query", "")),
                top_k=args.get("top_k"),
            )
        elif name == "memory_compile":
            err = _check_required(args, "target")
            if err:
                return err
            return memory_compile(
                config,
                target=str(args.get("target", "")),
                user=str(args["user"]) if args.get("user") is not None else None,
                task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
                branch=str(args["branch"]) if args.get("branch") is not None else None,
                include_scopes=args.get("include_scopes"),
                include_statuses=args.get("include_statuses"),
                preferred_tags=args.get("preferred_tags"),
                body_mode=str(args["body_mode"]) if args.get("body_mode") is not None else None,
                as_of=str(args["as_of"]) if args.get("as_of") is not None else None,
            )
        elif name == "memory_get_runtime_digest":
            return memory_get_runtime_digest(
                config,
                user=str(args["user"]) if args.get("user") is not None else None,
                task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
                branch=str(args["branch"]) if args.get("branch") is not None else None,
                max_chars=args.get("max_chars"),
            )
        elif name == "memory_validate_candidate":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_validate_candidate(
                config,
                str(args.get("record_id", "")),
                validated_by=str(args["validated_by"]) if args.get("validated_by") is not None else None,
            )
        elif name == "memory_publish_candidate":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_publish_candidate(
                config,
                str(args.get("record_id", "")),
                published_by=str(args["published_by"]) if args.get("published_by") is not None else None,
            )
        elif name == "memory_archive_record":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_archive_record(
                config,
                str(args.get("record_id", "")),
                reason=str(args["reason"]) if args.get("reason") is not None else None,
            )
        elif name == "memory_update_index":
            err = _check_required(args, "paths")
            if err:
                return err
            return memory_update_index(config, paths=args.get("paths", []))
        elif name == "memory_health_check":
            return memory_health_check(config)
        elif name == "memory_migrate_records":
            return memory_migrate_records(
                config,
                target_schema_version=str(args.get("target_schema_version", "1.0")),
            )
        elif name == "memory_delete_record":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_delete_record(
                config,
                str(args.get("record_id", "")),
                reason=str(args["reason"]) if args.get("reason") is not None else None,
            )
        elif name == "memory_record_observation":
            err = _check_required(args, "content_markdown")
            if err:
                return err
            return memory_record_observation(
                config,
                content_markdown=str(args.get("content_markdown", "")),
                author=str(args["author"]) if args.get("author") is not None else None,
                tags=args.get("tags"),
                confidence=args.get("confidence"),
                source_refs=args.get("source_refs"),
                task_id=str(args["task_id"]) if args.get("task_id") is not None else None,
                branch=str(args["branch"]) if args.get("branch") is not None else None,
                occurred_at=str(args["occurred_at"]) if args.get("occurred_at") is not None else None,
                memory_tier=str(args["memory_tier"]) if args.get("memory_tier") is not None else "hot",
                cognitive_level=str(args["cognitive_level"]) if args.get("cognitive_level") is not None else "shu",
                related_artifact_ids=args.get("related_artifact_ids"),
                asset_paths=args.get("asset_paths"),
                map_names=args.get("map_names"),
                plugin_names=args.get("plugin_names"),
                module_names=args.get("module_names"),
                class_names=args.get("class_names"),
                blueprint_paths=args.get("blueprint_paths"),
                system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
            )
        elif name == "memory_link_artifact":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_link_artifact(
                config,
                str(args.get("record_id", "")),
                related_artifact_ids=args.get("related_artifact_ids"),
                asset_paths=args.get("asset_paths"),
                map_names=args.get("map_names"),
                plugin_names=args.get("plugin_names"),
                module_names=args.get("module_names"),
                class_names=args.get("class_names"),
                blueprint_paths=args.get("blueprint_paths"),
                system_area=str(args["system_area"]) if args.get("system_area") is not None else None,
            )
        elif name == "memory_trace_lineage":
            err = _check_required(args, "record_id")
            if err:
                return err
            return memory_trace_lineage(
                config,
                str(args.get("record_id", "")),
                max_depth=args.get("max_depth"),
            )
        else:
            return error_result("unknown_tool", f"unknown tool: {name}")
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return error_result("internal_error", f"{exc}")


__all__ = [
    "_check_required",
    "_dispatch_memory_read",
    "_dispatch_memory_write",
    "_dispatch_memory_context",
    "_dispatch_memory_enhance",
    "_dispatch_tool",
]
