"""Compile-output writer + cache-key helpers.

Extracted from `memory_compiler.py` (P0-1 follow-up). Pure I/O layer:
- `cache_key`         : deterministic compile-cache JSON file name
- `write_compiled_view`: write rendered Markdown, refresh usage stats,
  drop a compile-cache manifest, and emit an audit event.

Both helpers are reused by every compile target (snapshot / digest /
review / rollback / runtime / handoff / system / publish) so they live
in their own module to avoid pulling the entire compiler when other
view modules need to write output.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .memory_compile_targets import slug_compile_value as _slug
from .memory_compiler_cache import record_usage_stats
from .memory_config import MemoryConfig
from .memory_corpus import CompilableRecord
from .memory_events import append_event
from .memory_paths import PathManager, PathSecurityError
from .memory_result import error_result, ok_result


def cache_key(
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


def write_compiled_view(
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
    record_usage_stats(config, included, used_at, target=target)
    cache_path = config.repo_root / ".ai-memory" / "compile-cache" / cache_key(
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


__all__ = ["cache_key", "write_compiled_view"]
