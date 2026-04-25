"""Memory MCP — administrative CLI.

A first-class command-line entry point for the maintenance / governance
operations that previously required ``mcp.expose_admin_tools=true`` or a
hand-written Python REPL snippet.

Examples (all run from the repository root)::

    python -m servers.memory_server.cli health
    python -m servers.memory_server.cli backup --path memory-bank/activeContext.md
    python -m servers.memory_server.cli compact --path memory-bank/activeContext.md \
        --policy hot_task_to_minimal --apply
    python -m servers.memory_server.cli compile --target runtime_digest

Output: every subcommand prints a JSON-encoded ``ok_result`` /
``error_result`` payload to stdout. Exit code is ``0`` on success and
``1`` on any non-ok result so the CLI is usable from CI / shell scripts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from .memory_backup import backup_files
from .memory_compactor import compact_memory
from .memory_compiler import memory_compile, memory_get_runtime_digest
from .memory_config import MemoryConfig, load_config
from .memory_governance import memory_archive_record, memory_publish_candidate, memory_validate_candidate
from .memory_guard import memory_guard_check
from .memory_maintenance import memory_delete_record, memory_health_check, memory_migrate_records
from .memory_record_index import memory_rebuild_index
from .memory_baseline import write_baseline as _write_baseline
from .memory_auto_maintenance import run_if_due as _run_if_due


# ── Helpers ────────────────────────────────────────────────────────────


def _emit(payload: dict[str, Any], pretty: bool) -> int:
    indent = 2 if pretty else None
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=True))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0 if payload.get("ok") else 1


def _resolve_root(arg_root: str | None) -> Path:
    if arg_root:
        return Path(arg_root).expanduser().resolve()
    return Path.cwd().resolve()


def _load(args: argparse.Namespace) -> MemoryConfig:
    return load_config(_resolve_root(args.root), config_path=args.config)


# ── Subcommand handlers ───────────────────────────────────────────────


def _cmd_guard(args: argparse.Namespace) -> dict[str, Any]:
    return memory_guard_check(_load(args))


def _cmd_health(args: argparse.Namespace) -> dict[str, Any]:
    return memory_health_check(_load(args))


def _cmd_rebuild_index(args: argparse.Namespace) -> dict[str, Any]:
    return memory_rebuild_index(_load(args))


def _cmd_scale_baseline(args: argparse.Namespace) -> dict[str, Any]:
    return _write_baseline(_load(args))


def _cmd_auto_maintenance(args: argparse.Namespace) -> dict[str, Any]:
    return _run_if_due(_load(args))


def _cmd_migrate(args: argparse.Namespace) -> dict[str, Any]:
    return memory_migrate_records(_load(args), target_schema_version=args.target_schema)


def _cmd_backup(args: argparse.Namespace) -> dict[str, Any]:
    if not args.path:
        return {"ok": False, "error": "invalid_input", "message": "at least one --path is required"}
    return backup_files(
        _load(args),
        paths=list(args.path),
        reason=args.reason,
        tag=args.tag,
    )


def _cmd_compact(args: argparse.Namespace) -> dict[str, Any]:
    return compact_memory(
        _load(args),
        path=args.path,
        policy=args.policy,
        dry_run=not args.apply,
        backup=not args.no_backup,
        archive_original=not args.no_archive_original,
        compress_to_tokens=args.compress_to_tokens,
    )


def _cmd_validate(args: argparse.Namespace) -> dict[str, Any]:
    return memory_validate_candidate(_load(args), args.record_id, validated_by=args.by)


def _cmd_publish(args: argparse.Namespace) -> dict[str, Any]:
    return memory_publish_candidate(_load(args), args.record_id, published_by=args.by)


def _cmd_archive(args: argparse.Namespace) -> dict[str, Any]:
    return memory_archive_record(_load(args), args.record_id, reason=args.reason)


def _cmd_delete(args: argparse.Namespace) -> dict[str, Any]:
    return memory_delete_record(_load(args), args.record_id, reason=args.reason)


def _cmd_compile(args: argparse.Namespace) -> dict[str, Any]:
    return memory_compile(
        _load(args),
        target=args.target,
        user=args.user,
        task_id=args.task_id,
        branch=args.branch,
        include_scopes=args.include_scopes,
        include_statuses=args.include_statuses,
        preferred_tags=args.preferred_tags,
        body_mode=args.body_mode,
        as_of=args.as_of,
    )


def _cmd_snapshot_rebuild(args: argparse.Namespace) -> dict[str, Any]:
    return memory_compile(
        _load(args),
        target="daily_snapshot",
        user=args.user,
        task_id=args.task_id,
        branch=args.branch,
        as_of=args.as_of,
    )


def _cmd_runtime_digest(args: argparse.Namespace) -> dict[str, Any]:
    return memory_get_runtime_digest(
        _load(args),
        user=args.user,
        task_id=args.task_id,
        branch=args.branch,
        max_chars=args.max_chars,
    )


# ── Parser ─────────────────────────────────────────────────────────────


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _csv_arg(value: str) -> list[str]:
    return _split_csv(value) or []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-cli",
        description="Memory MCP administrative CLI (guard / backup / compact / governance / compile).",
    )
    parser.add_argument("--root", help="Repository root (defaults to current working directory).")
    parser.add_argument("--config", help="Path to .ai-memory/config.json (defaults to <root>/.ai-memory/config.json).")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output (indent=2).")

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    sub.add_parser("guard", help="Run memory_guard_check.").set_defaults(func=_cmd_guard)
    sub.add_parser("health", help="Run memory_health_check.").set_defaults(func=_cmd_health)
    sub.add_parser("rebuild-index", help="Rebuild SQLite FTS index.").set_defaults(func=_cmd_rebuild_index)
    sub.add_parser("scale-baseline", help="Capture .ai-memory/baseline.json snapshot.").set_defaults(func=_cmd_scale_baseline)
    sub.add_parser("auto-maintenance", help="Run startup auto-maintenance if due.").set_defaults(func=_cmd_auto_maintenance)

    p_migrate = sub.add_parser("migrate", help="Migrate records to a target schema version.")
    p_migrate.add_argument("--target-schema", default="1.0", help="Target schema version (default: 1.0).")
    p_migrate.set_defaults(func=_cmd_migrate)

    p_backup = sub.add_parser("backup", help="Backup one or more files.")
    p_backup.add_argument("--path", action="append", required=True, help="Repository-relative path (can repeat).")
    p_backup.add_argument("--reason", help="Optional reason captured in the backup record.")
    p_backup.add_argument("--tag", help="Optional tag captured in the backup payload.")
    p_backup.set_defaults(func=_cmd_backup)

    p_compact = sub.add_parser("compact", help="Compact a hot file (dry-run by default).")
    p_compact.add_argument("--path", required=True)
    p_compact.add_argument("--policy", required=True)
    p_compact.add_argument("--apply", action="store_true", help="Disable dry-run and write the compacted file.")
    p_compact.add_argument("--no-backup", action="store_true", help="Skip backing up the original.")
    p_compact.add_argument("--no-archive-original", action="store_true")
    p_compact.add_argument("--compress-to-tokens", type=int)
    p_compact.set_defaults(func=_cmd_compact)

    p_validate = sub.add_parser("validate", help="Validate a candidate record.")
    p_validate.add_argument("record_id")
    p_validate.add_argument("--by", help="Reviewer username (must differ from author).")
    p_validate.set_defaults(func=_cmd_validate)

    p_publish = sub.add_parser("publish", help="Publish a validated candidate.")
    p_publish.add_argument("record_id")
    p_publish.add_argument("--by", help="Publisher username.")
    p_publish.set_defaults(func=_cmd_publish)

    p_archive = sub.add_parser("archive", help="Archive a record.")
    p_archive.add_argument("record_id")
    p_archive.add_argument("--reason")
    p_archive.set_defaults(func=_cmd_archive)

    p_delete = sub.add_parser("delete", help="Delete an archived record (writes a tombstone).")
    p_delete.add_argument("record_id")
    p_delete.add_argument("--reason")
    p_delete.set_defaults(func=_cmd_delete)

    p_compile = sub.add_parser("compile", help="Run memory_compile against a target view.")
    p_compile.add_argument("--target", required=True)
    p_compile.add_argument("--user")
    p_compile.add_argument("--task-id")
    p_compile.add_argument("--branch")
    p_compile.add_argument("--include-scopes", type=_csv_arg, help="Comma-separated scopes.")
    p_compile.add_argument("--include-statuses", type=_csv_arg, help="Comma-separated statuses.")
    p_compile.add_argument("--preferred-tags", type=_csv_arg, help="Comma-separated tags.")
    p_compile.add_argument("--body-mode")
    p_compile.add_argument("--as-of")
    p_compile.set_defaults(func=_cmd_compile)

    p_snap = sub.add_parser("snapshot-rebuild", help="Rebuild the daily snapshot for an optional as-of date.")
    p_snap.add_argument("--user")
    p_snap.add_argument("--task-id")
    p_snap.add_argument("--branch")
    p_snap.add_argument("--as-of")
    p_snap.set_defaults(func=_cmd_snapshot_rebuild)

    p_digest = sub.add_parser("runtime-digest", help="Read the cached runtime_digest view.")
    p_digest.add_argument("--user")
    p_digest.add_argument("--task-id")
    p_digest.add_argument("--branch")
    p_digest.add_argument("--max-chars", type=int)
    p_digest.set_defaults(func=_cmd_runtime_digest)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    handler: Callable[[argparse.Namespace], dict[str, Any]] = args.func
    try:
        result = handler(args)
    except Exception as exc:  # noqa: BLE001 - CLI surface needs structured error
        result = {"ok": False, "error": "cli_exception", "message": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, dict):
        result = {"ok": False, "error": "invalid_result", "message": "handler did not return a dict"}
    return _emit(result, pretty=args.pretty)


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
