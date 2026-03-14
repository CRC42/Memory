from __future__ import annotations

from pathlib import Path
from typing import Any

from .memory_config import MemoryConfig
from .memory_paths import PathManager, PathSecurityError
from .memory_result import error_result, ok_result
from .token_estimator import estimate_tokens


def check_total_budget(config: MemoryConfig, *, extra_chars: int = 0) -> dict[str, Any] | None:
    """Check if adding *extra_chars* would exceed the global memory budget.

    Returns an error_result dict when exceeded, else None (ok to proceed).
    Targets that don't exist or can't be read are counted as 0.
    """
    total_max_chars = config.guard_total_max_chars
    total_max_tokens = config.guard_total_max_tokens
    if total_max_chars is None and total_max_tokens is None:
        return None  # no global budget configured

    manager = PathManager(config)
    total_chars = 0
    total_tokens = 0
    for target in config.guard_targets:
        try:
            resolved = manager.resolve(target.path, must_exist=False, must_be_file=False)
        except Exception:
            continue
        if not resolved.exists() or not resolved.is_file():
            continue
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
            total_chars += len(text)
            total_tokens += estimate_tokens(text)
        except OSError:
            continue

    projected_chars = total_chars + extra_chars
    projected_tokens = total_tokens + int(extra_chars * 0.4)  # rough estimate for new content

    if total_max_chars is not None and projected_chars > total_max_chars:
        return error_result(
            "total_budget_exceeded",
            f"write would bring total to {projected_chars} chars (budget: {total_max_chars}). "
            "Compact or archive existing memory files first.",
            total_chars=total_chars,
            projected_chars=projected_chars,
            budget_chars=total_max_chars,
        )
    if total_max_tokens is not None and projected_tokens > total_max_tokens:
        return error_result(
            "total_budget_exceeded",
            f"write would bring total to ~{projected_tokens} tokens (budget: {total_max_tokens}). "
            "Compact or archive existing memory files first.",
            total_tokens=total_tokens,
            projected_tokens=projected_tokens,
            budget_tokens=total_max_tokens,
        )
    return None


def _default_suggestion(policy: str | None, status: str) -> str:
    if status == "missing":
        return "create target file before guard check can enforce thresholds"
    if status in {"warn", "exceeded"} and policy:
        return f"run memory_compact with policy={policy}"
    if status == "exceeded":
        return "compact or archive historical content"
    return "no action needed"


def memory_guard_check(config: MemoryConfig) -> dict:
    manager = PathManager(config)
    if not config.guard_targets:
        return ok_result("guard targets are empty", targets=[], stats={"total": 0})

    items: list[dict] = []
    stats = {"total": 0, "ok": 0, "warn": 0, "exceeded": 0, "missing": 0, "error": 0}

    for target in config.guard_targets:
        stats["total"] += 1
        max_chars = target.max_chars if target.max_chars is not None else config.guard_default_max_chars
        max_tokens = target.max_tokens if target.max_tokens is not None else config.guard_default_max_tokens

        try:
            resolved = manager.resolve(target.path, must_exist=False, must_be_file=False)
        except PathSecurityError as exc:
            entry = {
                "path": target.path,
                "chars": None,
                "tokens_est": None,
                "max_chars": max_chars,
                "max_tokens": max_tokens,
                "status": "error",
                "message": str(exc),
                "suggested_action": target.suggestion or _default_suggestion(target.policy, "error"),
            }
            items.append(entry)
            stats["error"] += 1
            continue

        if not resolved.exists():
            entry = {
                "path": target.path,
                "chars": 0,
                "tokens_est": 0,
                "max_chars": max_chars,
                "max_tokens": max_tokens,
                "status": "missing",
                "message": "target file does not exist",
                "suggested_action": target.suggestion or _default_suggestion(target.policy, "missing"),
            }
            items.append(entry)
            stats["missing"] += 1
            continue
        if not resolved.is_file():
            entry = {
                "path": target.path,
                "chars": None,
                "tokens_est": None,
                "max_chars": max_chars,
                "max_tokens": max_tokens,
                "status": "error",
                "message": "target is not a file",
                "suggested_action": target.suggestion or _default_suggestion(target.policy, "error"),
            }
            items.append(entry)
            stats["error"] += 1
            continue

        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            entry = {
                "path": target.path,
                "chars": None,
                "tokens_est": None,
                "max_chars": max_chars,
                "max_tokens": max_tokens,
                "status": "error",
                "message": f"failed to read file: {exc}",
                "suggested_action": target.suggestion or _default_suggestion(target.policy, "error"),
            }
            items.append(entry)
            stats["error"] += 1
            continue

        chars = len(text)
        tokens_est = estimate_tokens(text)

        over_char = max_chars is not None and chars > max_chars
        over_token = max_tokens is not None and tokens_est > max_tokens
        near_char = max_chars is not None and chars >= int(max_chars * 0.9)
        near_token = max_tokens is not None and tokens_est >= int(max_tokens * 0.9)

        if over_char or over_token:
            status = "exceeded"
        elif near_char or near_token:
            status = "warn"
        else:
            status = "ok"

        _STATUS_MESSAGES = {
            "ok": "within guard range",
            "warn": "approaching threshold (>=90%)",
            "exceeded": "threshold exceeded",
        }
        stats[status] += 1
        items.append(
            {
                "path": manager.to_repo_relative(resolved),
                "chars": chars,
                "tokens_est": tokens_est,
                "max_chars": max_chars,
                "max_tokens": max_tokens,
                "status": status,
                "message": _STATUS_MESSAGES.get(status, status),
                "suggested_action": target.suggestion or _default_suggestion(target.policy, status),
            }
        )

    # Compute total budget
    total_chars = sum(item["chars"] for item in items if item["chars"] is not None)
    total_tokens = sum(item["tokens_est"] for item in items if item["tokens_est"] is not None)

    total_status = "ok"
    total_message = "within total budget"

    if config.guard_total_max_chars is not None and total_chars > config.guard_total_max_chars:
        total_status = "exceeded"
        total_message = f"total chars {total_chars} exceeds budget {config.guard_total_max_chars}"
    elif config.guard_total_max_tokens is not None and total_tokens > config.guard_total_max_tokens:
        total_status = "exceeded"
        total_message = f"total tokens {total_tokens} exceeds budget {config.guard_total_max_tokens}"
    elif config.guard_total_max_chars is not None and total_chars >= int(config.guard_total_max_chars * 0.9):
        total_status = "warn"
        total_message = f"total chars {total_chars} approaching budget {config.guard_total_max_chars}"
    elif config.guard_total_max_tokens is not None and total_tokens >= int(config.guard_total_max_tokens * 0.9):
        total_status = "warn"
        total_message = f"total tokens {total_tokens} approaching budget {config.guard_total_max_tokens}"

    total_budget = {
        "total_chars": total_chars,
        "total_tokens_est": total_tokens,
        "max_chars": config.guard_total_max_chars,
        "max_tokens": config.guard_total_max_tokens,
        "status": total_status,
        "message": total_message,
    }

    return ok_result("guard check completed", targets=items, stats=stats, total_budget=total_budget)
