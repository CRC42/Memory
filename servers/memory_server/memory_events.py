from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig

# 缓存：避免每次调用都读文件
_vscode_user_cache: dict[str, str | None] = {}


def _read_vscode_username(repo_root: Path) -> str | None:
    """从 .vscode/settings.json 中读取 memory-mcp.userName 配置。

    结果按 repo_root 缓存，同一进程内只读一次文件。
    返回 None 表示未配置或读取失败。
    """
    cache_key = str(repo_root)
    if cache_key in _vscode_user_cache:
        return _vscode_user_cache[cache_key]

    result: str | None = None
    settings_path = repo_root / ".vscode" / "settings.json"
    try:
        if settings_path.is_file():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            value = data.get("memory-mcp.userName")
            if isinstance(value, str) and value.strip():
                result = value.strip()
    except (OSError, json.JSONDecodeError, ValueError):
        pass  # 读取/解析失败，静默回退

    _vscode_user_cache[cache_key] = result
    return result


def get_current_user(repo_root: Path | None = None) -> str:
    """获取当前用户名，完全无感。

    优先级：
    1. .vscode/settings.json 中的 "memory-mcp.userName"（需传入 repo_root）
    2. 环境变量 USERNAME（Windows）/ USER（POSIX）
    3. 回退到 'unknown'
    """
    # 优先从 .vscode/settings.json 读取
    if repo_root is not None:
        vscode_user = _read_vscode_username(repo_root)
        if vscode_user:
            return vscode_user

    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def _lock_file(handle) -> None:  # type: ignore[no-untyped-def]
    """Acquire an exclusive lock on the file handle (platform-aware)."""
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle) -> None:  # type: ignore[no-untyped-def]
    """Release the exclusive lock on the file handle (platform-aware)."""
    if sys.platform == "win32":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # lock already released
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_event(config: MemoryConfig, event_type: str, payload: dict[str, Any], status: str = "ok") -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "user": get_current_user(config.repo_root),
        "status": status,
        "payload": payload,
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"
    config.events_file.parent.mkdir(parents=True, exist_ok=True)
    _rotate_events_if_needed(config)
    with config.events_file.open("a", encoding="utf-8") as handle:
        _lock_file(handle)
        try:
            handle.write(line)
            handle.flush()
        finally:
            _unlock_file(handle)


# Default rotation thresholds; can be overridden by env var for emergencies.
_EVENTS_MAX_BYTES_DEFAULT = 5 * 1024 * 1024   # 5 MB per active file
_EVENTS_MAX_ARCHIVES_DEFAULT = 5              # keep last 5 rotated archives


def _events_max_bytes() -> int:
    raw = os.environ.get("MEMORY_MCP_EVENTS_MAX_BYTES")
    if raw and raw.isdigit():
        return max(1024, int(raw))
    return _EVENTS_MAX_BYTES_DEFAULT


def _events_max_archives() -> int:
    raw = os.environ.get("MEMORY_MCP_EVENTS_MAX_ARCHIVES")
    if raw and raw.isdigit():
        return max(0, int(raw))
    return _EVENTS_MAX_ARCHIVES_DEFAULT


def _rotate_events_if_needed(config: MemoryConfig) -> None:
    """Rotate events.jsonl when it grows past the configured size threshold.

    Rotated files are renamed `events.jsonl.YYYYMMDDTHHMMSS` next to the
    active file. The newest N archives are kept; older ones are deleted.
    Failures here must never break event recording, so all OS errors are
    swallowed and the active log keeps growing as a fallback.
    """
    try:
        active = config.events_file
        if not active.is_file():
            return
        max_bytes = _events_max_bytes()
        if active.stat().st_size < max_bytes:
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        archive = active.with_name(f"{active.name}.{ts}")
        # On Windows os.replace handles existing targets; pick a fresh name if needed.
        suffix = 1
        while archive.exists():
            archive = active.with_name(f"{active.name}.{ts}-{suffix}")
            suffix += 1
        os.replace(active, archive)
        active.touch()

        max_archives = _events_max_archives()
        if max_archives <= 0:
            return
        archives = sorted(
            [p for p in active.parent.iterdir() if p.is_file() and p.name.startswith(active.name + ".")]
        )
        while len(archives) > max_archives:
            try:
                archives[0].unlink()
            except OSError:
                break
            archives = archives[1:]
    except OSError:
        # Rotation must never block the audit write path.
        return
