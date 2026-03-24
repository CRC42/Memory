from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_ALLOWED_ROOTS = [".ai-context", "memory-bank"]
DEFAULT_EXCLUDED_DIRS = ["Binaries", "Intermediate", "DerivedDataCache", "Saved/Cooked"]

DEFAULT_CONFIG_CONTENT: dict[str, Any] = {
    "allowed_roots": DEFAULT_ALLOWED_ROOTS,
    "excluded_dirs": DEFAULT_EXCLUDED_DIRS,
    "max_file_size_bytes": 1_048_576,
    "skip_binary_files": True,
    "events_file": ".ai-memory/events.jsonl",
    "backups_dir": ".ai-memory/backups",
    "temp_dir": ".ai-memory/temp",
    "backup": {
        "max_total_bytes": 524_288,
        "max_file_bytes": 262_144,
        "max_batches": 5,
    },
    "guard": {
        "default_max_chars": 12_000,
        "default_max_tokens": 3_000,
        "total_max_chars": 60_000,
        "total_max_tokens": 15_000,
        "targets": [
            {"path": ".ai-context/current-task.md", "max_chars": 6_000, "policy": "hot_task", "role": "hot task context for current working session"},
            {"path": ".ai-context/latest-error.md", "max_chars": 4_000, "policy": "error_summary", "role": "latest valid error summary"},
            {"path": "memory-bank/activeContext.md", "max_chars": 8_000, "policy": "warm_context", "role": "current sprint focus, recent decisions, TODOs", "preferred_mode": "append"},
            {"path": "memory-bank/progress.md", "max_chars": 12_000, "policy": "warm_context", "role": "feature completion status, milestones"},
            {"path": "memory-bank/techContext.md", "max_chars": 10_000, "policy": "warm_context", "role": "tech stack, plugin matrix, architecture config"},
            {"path": "memory-bank/systemPatterns.md", "max_chars": 10_000, "policy": "warm_context", "role": "architecture patterns, coding conventions, design decisions"},
            {"path": "memory-bank/projectbrief.md", "max_chars": 8_000, "policy": "warm_context", "role": "project scope, core requirements, MVP goals"},
        ],
    },
}


@dataclass(frozen=True)
class GuardTarget:
    path: str
    max_chars: int | None
    max_tokens: int | None
    policy: str | None
    suggestion: str | None
    role: str | None = None


@dataclass(frozen=True)
class MemoryConfig:
    repo_root: Path
    config_path: Path
    allowed_roots: list[Path]
    excluded_dirs: list[str]
    max_file_size_bytes: int
    skip_binary_files: bool
    events_file: Path
    backups_dir: Path
    temp_dir: Path
    guard_default_max_chars: int | None
    guard_default_max_tokens: int | None
    guard_targets: list[GuardTarget]
    guard_total_max_chars: int | None = None
    guard_total_max_tokens: int | None = None
    backup_max_file_bytes: int | None = None
    backup_max_total_bytes: int | None = None
    backup_max_batches: int | None = None

    def repo_relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.repo_root).as_posix()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _to_repo_path(repo_root: Path, value: str) -> Path:
    return (repo_root / value).resolve()


def _parse_guard_targets(raw_targets: Any) -> list[GuardTarget]:
    if not isinstance(raw_targets, list):
        return []
    parsed: list[GuardTarget] = []
    for item in raw_targets:
        if isinstance(item, str):
            parsed.append(GuardTarget(path=item, max_chars=None, max_tokens=None, policy=None, suggestion=None))
            continue
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if not path:
            continue
        max_chars = item.get("max_chars")
        max_tokens = item.get("max_tokens")
        parsed.append(
            GuardTarget(
                path=path,
                max_chars=int(max_chars) if isinstance(max_chars, (int, float)) else None,
                max_tokens=int(max_tokens) if isinstance(max_tokens, (int, float)) else None,
                policy=str(item.get("policy")).strip() if item.get("policy") else None,
                suggestion=str(item.get("suggestion")).strip() if item.get("suggestion") else None,
                role=str(item.get("role")).strip() if item.get("role") else None,
            )
        )
    return parsed


def _ensure_layout(repo_root: Path) -> None:
    ai_memory = repo_root / ".ai-memory"
    ai_memory.mkdir(parents=True, exist_ok=True)
    (ai_memory / "backups").mkdir(parents=True, exist_ok=True)
    (ai_memory / "temp").mkdir(parents=True, exist_ok=True)
    events_file = ai_memory / "events.jsonl"
    if not events_file.exists():
        events_file.touch()


def _ensure_config_file(config_path: Path) -> None:
    if config_path.exists():
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(DEFAULT_CONFIG_CONTENT, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(repo_root: str | Path, config_path: str | Path | None = None) -> MemoryConfig:
    root = Path(repo_root).resolve()
    _ensure_layout(root)

    resolved_config_path = (Path(config_path).resolve() if config_path else (root / ".ai-memory/config.json").resolve())
    _ensure_config_file(resolved_config_path)

    loaded: dict[str, Any] = {}
    try:
        loaded = json.loads(resolved_config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        loaded = {}
    except json.JSONDecodeError:
        loaded = {}

    merged = _deep_merge(DEFAULT_CONFIG_CONTENT, loaded)
    guard = merged.get("guard", {}) if isinstance(merged.get("guard"), dict) else {}

    allowed_roots_raw = merged.get("allowed_roots", DEFAULT_ALLOWED_ROOTS)
    if not isinstance(allowed_roots_raw, list) or not allowed_roots_raw:
        allowed_roots_raw = DEFAULT_ALLOWED_ROOTS
    allowed_roots = [_to_repo_path(root, str(item)) for item in allowed_roots_raw]

    excluded_dirs_raw = merged.get("excluded_dirs", DEFAULT_EXCLUDED_DIRS)
    if not isinstance(excluded_dirs_raw, list):
        excluded_dirs_raw = DEFAULT_EXCLUDED_DIRS

    events_file = _to_repo_path(root, str(merged.get("events_file", ".ai-memory/events.jsonl")))
    backups_dir = _to_repo_path(root, str(merged.get("backups_dir", ".ai-memory/backups")))
    temp_dir = _to_repo_path(root, str(merged.get("temp_dir", ".ai-memory/temp")))
    events_file.parent.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    if not events_file.exists():
        events_file.touch()

    backup_cfg = merged.get("backup", {}) if isinstance(merged.get("backup"), dict) else {}

    return MemoryConfig(
        repo_root=root,
        config_path=resolved_config_path,
        allowed_roots=allowed_roots,
        excluded_dirs=[str(item).replace("\\", "/").strip("/") for item in excluded_dirs_raw if str(item).strip()],
        max_file_size_bytes=int(merged.get("max_file_size_bytes", 1_048_576)),
        skip_binary_files=bool(merged.get("skip_binary_files", True)),
        events_file=events_file,
        backups_dir=backups_dir,
        temp_dir=temp_dir,
        guard_default_max_chars=(
            int(guard.get("default_max_chars")) if isinstance(guard.get("default_max_chars"), (int, float)) else None
        ),
        guard_default_max_tokens=(
            int(guard.get("default_max_tokens")) if isinstance(guard.get("default_max_tokens"), (int, float)) else None
        ),
        guard_targets=_parse_guard_targets(guard.get("targets", [])),
        guard_total_max_chars=(
            int(guard.get("total_max_chars")) if isinstance(guard.get("total_max_chars"), (int, float)) else None
        ),
        guard_total_max_tokens=(
            int(guard.get("total_max_tokens")) if isinstance(guard.get("total_max_tokens"), (int, float)) else None
        ),
        backup_max_file_bytes=(
            int(backup_cfg.get("max_file_bytes")) if isinstance(backup_cfg.get("max_file_bytes"), (int, float)) else None
        ),
        backup_max_total_bytes=(
            int(backup_cfg.get("max_total_bytes")) if isinstance(backup_cfg.get("max_total_bytes"), (int, float)) else None
        ),
        backup_max_batches=(
            int(backup_cfg.get("max_batches")) if isinstance(backup_cfg.get("max_batches"), (int, float)) else None
        ),
    )
