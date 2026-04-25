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
        return memory_retrieve_context(
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
    return error_result(
        "invalid_input",
        "operation must be one of: compile, runtime_digest, trace_lineage, list_conflicts, compare_snapshots, retrieve_context, important_memories, config_diagnose",
    )


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
    "_dispatch_tool",
]
