from __future__ import annotations

import re

STANDARD_TARGETS = {"runtime_digest", "task_handoff", "system_digest", "publish_queue"}
SNAPSHOT_TARGETS = {"daily_snapshot", "weekly_snapshot", "monthly_snapshot"}
ROLE_TARGETS = {"rollback_context", "review_queue", "dao_digest", "fa_digest", "shu_digest"}
SUPPORTED_TARGETS = STANDARD_TARGETS | SNAPSHOT_TARGETS | ROLE_TARGETS
SUPPORTED_BODY_MODES = {"compact", "full"}
DEFAULT_BODY_MODE = "compact"
DEFAULT_INCLUDE_SCOPES = ["shared", "personal"]
DEFAULT_INCLUDE_STATUSES = ["validated", "published"]
DEFAULT_CONTEXT_SCOPES = ["shared", "personal", "session", "task_or_branch", "project_shared", "org_shared"]
DIGEST_LEVELS = {
    "dao_digest": "dao",
    "fa_digest": "fa",
    "shu_digest": "shu",
}


def slug_compile_value(value: str, *, fallback: str) -> str:
    normalized = value.replace("\\", "/")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", normalized).strip("-._")
    return slug or fallback


def compiled_path(target: str, *, user: str | None = None, task_id: str | None = None, branch: str | None = None) -> str:
    if target == "daily_snapshot":
        label = slug_compile_value(task_id or "daily", fallback="daily")
        return f"memory-bank/compiled/snapshots/daily/{label}.md"
    if target == "weekly_snapshot":
        label = slug_compile_value(task_id or "weekly", fallback="weekly")
        return f"memory-bank/compiled/snapshots/weekly/{label}.md"
    if target == "monthly_snapshot":
        label = slug_compile_value(task_id or "monthly", fallback="monthly")
        return f"memory-bank/compiled/snapshots/monthly/{label}.md"
    if target == "review_queue":
        return "memory-bank/compiled/review/review-queue.md"
    if target == "dao_digest":
        return "memory-bank/compiled/runtime/dao-digest.md"
    if target == "fa_digest":
        return "memory-bank/compiled/runtime/fa-digest.md"
    if target == "shu_digest":
        return "memory-bank/compiled/runtime/shu-digest.md"
    if target == "rollback_context":
        if task_id:
            return f"memory-bank/compiled/runtime/task/{slug_compile_value(task_id, fallback='task')}-rollback.md"
        if branch:
            return f"memory-bank/compiled/runtime/branch/{slug_compile_value(branch, fallback='branch')}-rollback.md"
        if user:
            return f"memory-bank/compiled/runtime/people/{slug_compile_value(user, fallback='user')}-rollback.md"
        return "memory-bank/compiled/runtime/rollback-context.md"
    if target == "task_handoff":
        task_slug = slug_compile_value(task_id or "handoff", fallback="handoff")
        return f"memory-bank/compiled/runtime/task/{task_slug}-handoff.md"
    if target == "publish_queue":
        return "memory-bank/compiled/publish/publish-queue.md"
    if target == "system_digest":
        return "memory-bank/compiled/runtime/system-digest.md"
    if task_id:
        return f"memory-bank/compiled/runtime/task/{slug_compile_value(task_id, fallback='task')}.md"
    if branch:
        return f"memory-bank/compiled/runtime/branch/{slug_compile_value(branch, fallback='branch')}.md"
    if user:
        return f"memory-bank/compiled/runtime/people/{slug_compile_value(user, fallback='user')}-digest.md"
    return "memory-bank/compiled/runtime/system-digest.md"
