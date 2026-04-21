"""
memory_write — controlled write tool for memory files.

Safety features:
    - Path whitelist (allowed_roots) enforced by PathManager
    - Auto-backup before overwrite (configurable)
    - Guard check after write to warn on capacity overflow
    - Audit event logged for every write
    - Atomic write via temp file + os.replace
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_backup import backup_files
from .memory_config import MemoryConfig
from .memory_events import append_event, get_current_user
from .memory_guard import check_total_budget
from .memory_paths import PathManager, PathSecurityError, resolve_user_path
from .memory_result import error_result, ok_result
from .token_estimator import estimate_tokens


def _lookup_write_policy(config: MemoryConfig, path: str) -> str | None:
    """查找目标路径对应的 write_policy。

    优先匹配 guard_targets 中的 write_policy，
    其次匹配 multi_user.shared_paths_policy。
    返回 None 表示无特殊策略。
    """
    # 1. guard targets 中的 write_policy 优先
    normalized = path.replace("\\", "/").strip("/")
    for target in config.guard_targets:
        tp = target.path.replace("\\", "/").strip("/")
        if tp == normalized or normalized.endswith(tp):
            if target.write_policy:
                return target.write_policy
    # 2. multi_user.shared_paths_policy 兜底
    if config.multi_user and config.multi_user.enabled and config.multi_user.shared_paths_policy:
        for sp, policy in config.multi_user.shared_paths_policy.items():
            sp_norm = sp.replace("\\", "/").strip("/")
            if sp_norm == normalized or normalized.endswith(sp_norm):
                return policy
    return None


def memory_write(
    config: MemoryConfig,
    path: str,
    content: str,
    *,
    mode: str = "overwrite",
    backup: bool = True,
    create_if_missing: bool = True,
    reason: str | None = None,
    inject_user_tag: bool | None = None,
) -> dict[str, Any]:
    """Write content to a memory file with safety controls.

    Args:
        config: MemoryConfig instance.
        path: Target file path (must be within allowed_roots).
        content: The content to write.
        mode: "overwrite" (replace entire file) or "append" (add to end).
        backup: Whether to auto-backup before writing (default True).
        create_if_missing: Create the file if it doesn't exist (default True).
        reason: Optional reason for the write (logged in audit event).
        inject_user_tag: Whether to inject an HTML comment with the writing
            user and timestamp into the file content. When None (default), the
            writer auto-detects: Markdown files (`.md`/`.markdown`) get the
            tag for traceability, every other extension is left untouched so
            JSON / YAML / TOML / source files cannot be silently corrupted.
            Pass False to force-disable injection even for Markdown.

    Returns:
        Result dict with ok/error status and metadata.
    """
    # Validate mode
    if mode not in ("overwrite", "append"):
        return error_result("invalid_input", "mode must be 'overwrite' or 'append'")

    if not content and mode == "overwrite":
        return error_result("invalid_input", "content must not be empty for overwrite mode")

    # write_policy 检查：append_only 强制降级 / user_scoped 路径重定向
    policy_override: str | None = None
    effective_mode = mode
    effective_path = path
    _write_policy = _lookup_write_policy(config, path)
    if _write_policy == "append_only" and mode == "overwrite":
        effective_mode = "append"
        policy_override = "append_only"
    elif _write_policy == "user_scoped":
        current_user_for_path = get_current_user(config.repo_root)
        if not current_user_for_path or current_user_for_path == "unknown":
            return error_result(
                "user_required",
                "user_scoped write requires a valid user identity. "
                "Set 'memory-mcp.userName' in .vscode/settings.json or ensure USERNAME env var is set.",
            )
        effective_path = resolve_user_path(config, path, current_user_for_path)

    manager = PathManager(config)

    # Resolve and validate path security（使用 effective_path，可能已被 user_scoped 重定向）
    try:
        resolved = manager.resolve(
            effective_path,
            must_exist=not create_if_missing,
            must_be_file=False,
        )
    except PathSecurityError as exc:
        return error_result("path_not_allowed", str(exc))
    except FileNotFoundError as exc:
        return error_result("not_found", str(exc))

    # If it exists, ensure it's a file (not a directory)
    if resolved.exists() and not resolved.is_file():
        return error_result("invalid_path", f"target is not a file: {effective_path}")

    file_exists = resolved.exists()
    rel_path = manager.to_repo_relative(resolved)

    # Read original content for diff metadata
    original_content = ""
    if file_exists:
        try:
            original_content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return error_result("read_failed", f"failed to read existing file: {exc}")

    # Pre-write global budget check
    net_new_chars = len(content) - len(original_content) if effective_mode == "overwrite" else len(content)
    if net_new_chars > 0:
        budget_err = check_total_budget(config, extra_chars=net_new_chars)
        if budget_err is not None:
            return budget_err

    # Auto-backup before writing (only if file exists)
    backup_result: dict[str, Any] | None = None
    if backup and file_exists:
        backup_result = backup_files(
            config,
            [rel_path],
            reason=reason or "memory_write auto-backup",
            tag="pre_write",
            event_type="memory_backup",
            write_event=True,
        )
        if not backup_result.get("ok"):
            return error_result(
                "backup_failed",
                f"auto-backup failed before write: {backup_result.get('message', 'unknown')}",
            )

    # Build final content
    current_user = get_current_user(config.repo_root)
    suffix = resolved.suffix.lower()
    if inject_user_tag is None:
        tag_enabled = suffix in {".md", ".markdown"}
    else:
        tag_enabled = bool(inject_user_tag)
    if effective_mode == "append":
        if tag_enabled:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            user_header = f"\n<!-- written by {current_user} at {timestamp} -->\n"
            separator = "" if original_content.endswith("\n") or not original_content else "\n"
            final_content = original_content + separator + user_header + content
        else:
            separator = "" if original_content.endswith("\n") or not original_content else "\n"
            final_content = original_content + separator + content
    else:
        if tag_enabled:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            footer = f"\n<!-- last overwritten by {current_user} at {timestamp} -->\n"
            final_content = content.rstrip("\n") + "\n" + footer
        else:
            final_content = content

    # Ensure final content ends with newline
    if final_content and not final_content.endswith("\n"):
        final_content += "\n"

    # Atomic write via temp file
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp_name = f"{resolved.name}.{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}.tmp"
    temp_path = (config.temp_dir / temp_name).resolve()
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        temp_path.write_text(final_content, encoding="utf-8")
        os.replace(temp_path, resolved)
    except OSError as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return error_result("write_failed", f"failed to write file: {exc}")

    # Compute metadata
    after_chars = len(final_content)
    after_tokens = estimate_tokens(final_content)
    before_chars = len(original_content)
    before_tokens = estimate_tokens(original_content)

    # Log audit event（自动包含 user 字段，由 append_event 注入）
    append_event(
        config,
        event_type="memory_write",
        payload={
            "path": rel_path,
            "mode": effective_mode,
            "original_mode": mode if policy_override else None,
            "policy_override": policy_override,
            "reason": reason,
            "backup": backup,
            "created": not file_exists,
            "before": {"chars": before_chars, "tokens_est": before_tokens},
            "after": {"chars": after_chars, "tokens_est": after_tokens},
            "batch_id": backup_result.get("batch_id") if backup_result else None,
            "written_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # Check guard threshold for the written file
    guard_warning: str | None = None
    for target in config.guard_targets:
        if target.path == rel_path or rel_path.endswith(target.path):
            max_chars = target.max_chars if target.max_chars is not None else config.guard_default_max_chars
            max_tokens = target.max_tokens if target.max_tokens is not None else config.guard_default_max_tokens
            if max_chars is not None and after_chars > max_chars:
                guard_warning = f"exceeds max_chars ({after_chars}/{max_chars})"
            elif max_tokens is not None and after_tokens > max_tokens:
                guard_warning = f"exceeds max_tokens ({after_tokens}/{max_tokens})"
            elif max_chars is not None and after_chars >= int(max_chars * 0.9):
                guard_warning = f"near max_chars threshold ({after_chars}/{max_chars})"
            elif max_tokens is not None and after_tokens >= int(max_tokens * 0.9):
                guard_warning = f"near max_tokens threshold ({after_tokens}/{max_tokens})"
            break

    result = ok_result(
        "write completed",
        path=rel_path,
        mode=effective_mode,
        created=not file_exists,
        before={"chars": before_chars, "tokens_est": before_tokens},
        after={"chars": after_chars, "tokens_est": after_tokens},
        backup_batch_id=backup_result.get("batch_id") if backup_result else None,
        guard_warning=guard_warning,
        reason=reason,
    )
    if policy_override:
        result["original_mode"] = mode
        result["policy_override"] = policy_override
    return result
