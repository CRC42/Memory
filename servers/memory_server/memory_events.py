from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from .memory_config import MemoryConfig


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
        "status": status,
        "payload": payload,
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"
    config.events_file.parent.mkdir(parents=True, exist_ok=True)
    with config.events_file.open("a", encoding="utf-8") as handle:
        _lock_file(handle)
        try:
            handle.write(line)
            handle.flush()
        finally:
            _unlock_file(handle)
