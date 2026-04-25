from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_ALLOWED_ROOTS = [".ai-context", "memory-bank"]
DEFAULT_EXCLUDED_DIRS = ["Binaries", "Intermediate", "DerivedDataCache", "Saved/Cooked"]

# Single source of truth for the built-in tag controlled vocabulary.
# memory_records imports this list so both the runtime validator and the
# default config stay in sync (previously they drifted as two parallel lists).
DEFAULT_ALLOWED_TAGS: list[str] = [
    "archive_candidate",
    "asset_pipeline",
    "build",
    "handoff_ready",
    "high_value",
    "material",
    "mcp",
    "needs_validation",
    "skill_possible",
    "texture",
    "ui",
    "validation",
    "workflow",
]

DEFAULT_CONFIG_CONTENT: dict[str, Any] = {
    "allowed_roots": DEFAULT_ALLOWED_ROOTS,
    "excluded_dirs": DEFAULT_EXCLUDED_DIRS,
    "max_file_size_bytes": 1_048_576,
    "skip_binary_files": True,
    "events_file": ".ai-memory/events.jsonl",
    "backups_dir": ".ai-memory/backups",
    "temp_dir": ".ai-memory/temp",
    "multi_user": {
        "enabled": True,
        "user_scoped_paths": [
            "memory-bank/activeContext.md",
        ],
        "shared_paths_policy": {
            "memory-bank/progress.md": "append_only",
            "memory-bank/techContext.md": "append_only",
            "memory-bank/systemPatterns.md": "append_only",
            "memory-bank/projectbrief.md": "append_only",
        },
    },
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
            {"path": "memory-bank/activeContext.md", "max_chars": 8_000, "policy": "warm_context", "role": "current sprint focus, recent decisions, TODOs", "write_policy": "user_scoped"},
            {"path": "memory-bank/progress.md", "max_chars": 12_000, "policy": "warm_context", "role": "feature completion status, milestones", "write_policy": "append_only"},
            {"path": "memory-bank/techContext.md", "max_chars": 10_000, "policy": "warm_context", "role": "tech stack, plugin matrix, architecture config", "write_policy": "append_only"},
            {"path": "memory-bank/systemPatterns.md", "max_chars": 10_000, "policy": "warm_context", "role": "architecture patterns, coding conventions, design decisions", "write_policy": "append_only"},
            {"path": "memory-bank/projectbrief.md", "max_chars": 8_000, "policy": "warm_context", "role": "project scope, core requirements, MVP goals", "write_policy": "append_only"},
        ],
    },
    "governance": {
        "min_confidence": 0.0,
        "require_source_refs_for": [],
        "publish_owners": [],
        "reviewers": [],
    },
    "tag_schema": {
        "allowed_tags": list(DEFAULT_ALLOWED_TAGS),
        "version": "v1",
    },
    "mcp": {
        "expose_admin_tools": False,
        "fsync_strict": False,
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
    write_policy: str | None = None  # "append_only" | "user_scoped" | None


@dataclass(frozen=True)
class MultiUserConfig:
    """多人协作配置。"""
    enabled: bool = False
    user_scoped_paths: list[str] | None = None  # 需要按用户分区的路径列表
    shared_paths_policy: dict[str, str] | None = None  # 共享路径 → 写入策略映射


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
    multi_user: MultiUserConfig | None = None
    governance_min_confidence: float = 0.0
    governance_require_source_refs_for: list[str] | None = None
    governance_publish_owners: list[str] | None = None
    governance_reviewers: list[str] | None = None
    tag_allowed_tags: list[str] | None = None
    tag_schema_version: str = "v1"
    mcp_expose_admin_tools: bool = False
    mcp_fsync_strict: bool = False

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
                write_policy=str(item.get("write_policy")).strip() if item.get("write_policy") else None,
            )
        )
    return parsed


def _parse_multi_user(raw: Any) -> MultiUserConfig | None:
    """解析 multi_user 配置节。"""
    if not isinstance(raw, dict):
        return None
    enabled = bool(raw.get("enabled", False))
    user_scoped_paths = raw.get("user_scoped_paths")
    if isinstance(user_scoped_paths, list):
        user_scoped_paths = [str(p).strip() for p in user_scoped_paths if str(p).strip()]
    else:
        user_scoped_paths = None
    shared_paths_policy = raw.get("shared_paths_policy")
    if isinstance(shared_paths_policy, dict):
        shared_paths_policy = {str(k).strip(): str(v).strip() for k, v in shared_paths_policy.items() if str(k).strip()}
    else:
        shared_paths_policy = None
    return MultiUserConfig(
        enabled=enabled,
        user_scoped_paths=user_scoped_paths,
        shared_paths_policy=shared_paths_policy,
    )


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
    governance_cfg = merged.get("governance", {}) if isinstance(merged.get("governance"), dict) else {}
    tag_schema_cfg = merged.get("tag_schema", {}) if isinstance(merged.get("tag_schema"), dict) else {}
    mcp_cfg = merged.get("mcp", {}) if isinstance(merged.get("mcp"), dict) else {}

    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

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
        multi_user=_parse_multi_user(merged.get("multi_user")),
        governance_min_confidence=(
            float(governance_cfg.get("min_confidence"))
            if isinstance(governance_cfg.get("min_confidence"), (int, float))
            else 0.0
        ),
        governance_require_source_refs_for=_string_list(governance_cfg.get("require_source_refs_for")),
        governance_publish_owners=_string_list(governance_cfg.get("publish_owners")),
        governance_reviewers=_string_list(governance_cfg.get("reviewers")),
        tag_allowed_tags=_string_list(tag_schema_cfg.get("allowed_tags")),
        tag_schema_version=str(tag_schema_cfg.get("version", "v1")),
        mcp_expose_admin_tools=bool(mcp_cfg.get("expose_admin_tools", False)),
        mcp_fsync_strict=bool(mcp_cfg.get("fsync_strict", False)),
    )
