from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig
from .memory_locks import file_lock

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
    1. 环境变量 ``MEMORY_MCP_USER``（CI / 子进程 / 测试稳定注入；最高优先级）
    2. .vscode/settings.json 中的 "memory-mcp.userName"（需传入 repo_root）
    3. 环境变量 USERNAME（Windows）/ USER（POSIX）
    4. 回退到 'unknown'
    """
    # 1. 显式覆盖：CI / 测试 / 子进程注入稳定 user
    explicit = os.environ.get("MEMORY_MCP_USER")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    # 2. .vscode/settings.json
    if repo_root is not None:
        vscode_user = _read_vscode_username(repo_root)
        if vscode_user:
            return vscode_user

    # 3. OS 账号
    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


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
    # Cross-process exclusive lock for the whole rotate-then-append
    # critical section. Without this, two processes can race in
    # ``_rotate_events_if_needed`` (one renames the active file while
    # the other is mid-write into a now-orphaned handle), losing
    # events. The Windows ``msvcrt.locking(fd, LK_LOCK, 1)`` approach
    # locks 1 byte at the *current* file position; with two processes
    # opening the file in append mode at different EOF offsets, the
    # byte ranges do not overlap and the lock provides no exclusion.
    with file_lock(config.repo_root, config.events_file):
        _rotate_events_if_needed(config)
        with config.events_file.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            # Durability: ensure the audit line reaches disk before the
            # caller observes "ok". Best-effort by default; in
            # ``mcp.fsync_strict`` mode an OSError propagates so
            # callers can surface it.
            try:
                os.fsync(handle.fileno())
            except OSError:
                if getattr(config, "mcp_fsync_strict", False):
                    raise


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


def count_recent_events(
    config: MemoryConfig,
    event_type: str,
    *,
    window_seconds: int = 24 * 3600,
) -> int:
    """Return the count of ``event_type`` rows within the last ``window_seconds``.

    Walks ``events.jsonl`` plus any rotated archives; tolerant of partial /
    malformed lines so the operational health surface never fails just
    because the audit log has been rotated mid-write.
    """

    try:
        active = config.events_file
    except Exception:
        return 0
    if not isinstance(active, Path):
        return 0
    cutoff = datetime.now(timezone.utc).timestamp() - max(0, int(window_seconds))
    candidates: list[Path] = []
    if active.is_file():
        candidates.append(active)
    parent = active.parent
    if parent.is_dir():
        try:
            candidates.extend(
                p for p in parent.iterdir()
                if p.is_file() and p.name.startswith(active.name + ".")
            )
        except OSError:
            pass
    count = 0
    for path in candidates:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or '"event_type"' not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if record.get("event_type") != event_type:
                        continue
                    ts_raw = record.get("ts")
                    if not isinstance(ts_raw, str):
                        continue
                    try:
                        ts = datetime.fromisoformat(ts_raw).timestamp()
                    except ValueError:
                        continue
                    if ts >= cutoff:
                        count += 1
        except OSError:
            continue
    return count
