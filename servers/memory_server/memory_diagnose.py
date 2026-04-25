"""P1-4: config diagnostics (v0.6.0 OOTB hardening).

Reports the *effective* value and *source* of key memory-mcp config
fields so users can answer "why is this setting what it is?" without
reading source.

Source labels:
- ``default`` — dataclass default; no override observed
- ``file``    — set in ``.ai-memory/config.json``
- ``env``     — overridden by environment variable
- ``vscode``  — derived from ``.vscode/settings.json`` (e.g. user id)

Pure read-only function; never raises.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig


def _read_raw_config(repo_root: Path) -> dict[str, Any]:
    cfg_path = repo_root / ".ai-memory" / "config.json"
    if not cfg_path.is_file():
        return {}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_vscode_user(repo_root: Path) -> str | None:
    path = repo_root / ".vscode" / "settings.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    val = data.get("memory-mcp.userName")
    return val if isinstance(val, str) and val.strip() else None


def config_diagnose(config: MemoryConfig) -> dict[str, Any]:
    """Return a dict of ``{field: {value, source, override_hint}}``."""
    raw = _read_raw_config(config.repo_root)
    mcp_raw = raw.get("mcp") if isinstance(raw.get("mcp"), dict) else {}

    fields: dict[str, dict[str, Any]] = {}

    def report(field: str, value: Any, source: str, hint: str | None = None) -> None:
        entry: dict[str, Any] = {"value": value, "source": source}
        if hint:
            entry["override_hint"] = hint
        fields[field] = entry

    # mcp.allow_unknown_user
    if "allow_unknown_user" in mcp_raw:
        report("mcp.allow_unknown_user", config.mcp_allow_unknown_user, "file",
               "set mcp.allow_unknown_user in .ai-memory/config.json")
    else:
        report("mcp.allow_unknown_user", config.mcp_allow_unknown_user, "default")

    # mcp.shared_overwrite_policy
    if "shared_overwrite_policy" in mcp_raw:
        report("mcp.shared_overwrite_policy", config.mcp_shared_overwrite_policy, "file",
               "values: 'reject' | 'downgrade'")
    else:
        report("mcp.shared_overwrite_policy", config.mcp_shared_overwrite_policy, "default")

    # mcp.auto_maintenance.*
    am_raw = mcp_raw.get("auto_maintenance") if isinstance(mcp_raw.get("auto_maintenance"), dict) else {}
    am_value = config.mcp_auto_maintenance or {}
    report(
        "mcp.auto_maintenance.enabled",
        am_value.get("enabled", True),
        "file" if "enabled" in am_raw else "default",
    )

    # mcp.fsync_strict (env override common)
    fsync_env = os.environ.get("MEMORY_MCP_FSYNC_STRICT")
    if fsync_env is not None:
        report("mcp.fsync_strict", getattr(config, "mcp_fsync_strict", False), "env",
               "unset MEMORY_MCP_FSYNC_STRICT to revert to file/default")
    elif "fsync_strict" in mcp_raw:
        report("mcp.fsync_strict", getattr(config, "mcp_fsync_strict", False), "file")
    else:
        report("mcp.fsync_strict", getattr(config, "mcp_fsync_strict", False), "default")

    # multi_user.enabled
    mu_raw = raw.get("multi_user") if isinstance(raw.get("multi_user"), dict) else {}
    if "enabled" in mu_raw:
        report("multi_user.enabled", getattr(config, "multi_user_enabled", False), "file")
    else:
        report("multi_user.enabled", getattr(config, "multi_user_enabled", False), "default")

    # Effective user id
    vscode_user = _read_vscode_user(config.repo_root)
    if vscode_user:
        report("user.effective", vscode_user, "vscode",
               "edit memory-mcp.userName in .vscode/settings.json")
    elif os.environ.get("USERNAME") or os.environ.get("USER"):
        report(
            "user.effective",
            os.environ.get("USERNAME") or os.environ.get("USER"),
            "env",
            "set memory-mcp.userName in .vscode/settings.json to override",
        )
    else:
        report("user.effective", "unknown", "default",
               "set memory-mcp.userName in .vscode/settings.json")

    return {
        "ok": True,
        "repo_root": str(config.repo_root),
        "fields": fields,
    }
