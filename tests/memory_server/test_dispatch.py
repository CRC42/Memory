"""Tests for _dispatch_tool to verify the dispatch layer (server.py).

These tests complement the direct-function tests by ensuring arguments
pass correctly through the dispatch routing, including edge cases
that only manifest at the dispatch level.
"""

from __future__ import annotations

from pathlib import Path

from servers.memory_server.memory_config import load_config
from servers.memory_server.server import _dispatch_tool


def test_dispatch_write_append_empty_content(repo: Path) -> None:
    """P1 regression: append with empty content must NOT be rejected by _check_required."""
    config = load_config(repo)
    result = _dispatch_tool(config, "memory_write", {
        "path": "memory-bank/notes.md",
        "content": "",
        "mode": "append",
    })
    assert result["ok"] is True, f"Expected ok=True, got: {result}"
    assert result["mode"] == "append"


def test_dispatch_get(repo: Path) -> None:
    """memory_get through dispatch returns file content."""
    config = load_config(repo)
    result = _dispatch_tool(config, "memory_get", {"path": "memory-bank/notes.md"})
    assert result["ok"] is True
    assert "Boss Notes" in result["content"]


def test_dispatch_search(repo: Path) -> None:
    """memory_search through dispatch."""
    config = load_config(repo)
    result = _dispatch_tool(config, "memory_search", {"query": "Boss"})
    assert result["ok"] is True
    assert result["stats"]["total_hits"] >= 1


def test_dispatch_guard(repo: Path) -> None:
    """memory_guard_check through dispatch."""
    config = load_config(repo)
    result = _dispatch_tool(config, "memory_guard_check", {})
    assert result["ok"] is True
    assert "targets" in result


def test_dispatch_backup(repo: Path) -> None:
    """memory_backup through dispatch."""
    config = load_config(repo)
    result = _dispatch_tool(config, "memory_backup", {"paths": ["memory-bank/notes.md"]})
    assert result["ok"] is True
    assert "batch_id" in result


def test_dispatch_compact(repo: Path) -> None:
    """memory_compact through dispatch with dry_run."""
    config = load_config(repo)
    result = _dispatch_tool(config, "memory_compact", {
        "path": ".ai-context/current-task.md",
        "policy": "hot_task",
        "dry_run": True,
    })
    assert result["ok"] is True
    assert result["dry_run"] is True


def test_dispatch_unknown_tool(repo: Path) -> None:
    """Unknown tool name returns error."""
    config = load_config(repo)
    result = _dispatch_tool(config, "nonexistent_tool", {})
    assert result["ok"] is False
    assert result["error"] == "unknown_tool"


def test_dispatch_missing_required_param(repo: Path) -> None:
    """Missing required param returns error, not crash."""
    config = load_config(repo)
    # memory_get requires "path"
    result = _dispatch_tool(config, "memory_get", {})
    assert result["ok"] is False
    assert "path" in result["message"]


def test_dispatch_write_overwrite_empty_content_rejected(repo: Path) -> None:
    """Overwrite with empty content should be rejected by the function, not dispatch."""
    config = load_config(repo)
    result = _dispatch_tool(config, "memory_write", {
        "path": "memory-bank/notes.md",
        "content": "",
        "mode": "overwrite",
    })
    assert result["ok"] is False
    assert "empty" in result["message"].lower()


def test_dispatch_write_none_content_rejected(repo: Path) -> None:
    """content=None should be caught by _check_required."""
    config = load_config(repo)
    result = _dispatch_tool(config, "memory_write", {
        "path": "memory-bank/notes.md",
        "content": None,
    })
    assert result["ok"] is False
    assert "content" in result["message"]
