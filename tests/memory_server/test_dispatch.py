"""Tests for _dispatch_tool to verify the dispatch layer (server.py).

These tests complement the direct-function tests by ensuring arguments
pass correctly through the dispatch routing, including edge cases
that only manifest at the dispatch level.
"""

from __future__ import annotations

from pathlib import Path

from servers.memory_server.memory_config import load_config
from servers.memory_server.memory_records import memory_write_record
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


def test_dispatch_facade_read_get_and_search(repo: Path) -> None:
    config = load_config(repo)

    get_result = _dispatch_tool(config, "memory_read", {"operation": "get", "path": "memory-bank/notes.md"})
    search_result = _dispatch_tool(config, "memory_read", {"operation": "search", "query": "Boss"})

    assert get_result["ok"] is True
    assert "Boss Notes" in get_result["content"]
    assert search_result["ok"] is True
    assert search_result["stats"]["total_hits"] >= 1


def test_dispatch_facade_write_record_and_context_compile(repo: Path) -> None:
    config = load_config(repo)

    record = _dispatch_tool(
        config,
        "memory_write",
        {
            "operation": "record",
            "content_markdown": "# Facade Record\n\nCompiled through facade tools.\n",
            "record_kind": "note",
            "scope": "personal",
            "status": "validated",
            "author": "alice",
            "tags": ["mcp"],
            "task_id": "task_facade",
        },
    )
    compiled = _dispatch_tool(
        config,
        "memory_context",
        {
            "operation": "compile",
            "target": "runtime_digest",
            "user": "alice",
            "task_id": "task_facade",
        },
    )
    fetched = _dispatch_tool(
        config,
        "memory_read",
        {"operation": "runtime_digest", "user": "alice", "task_id": "task_facade"},
    )

    assert record["ok"] is True
    assert compiled["ok"] is True
    assert fetched["ok"] is True
    assert record["id"] in compiled["included_record_ids"]
    assert fetched["content"] == compiled["content"]


def test_dispatch_facade_write_observation_and_trace_lineage(repo: Path) -> None:
    config = load_config(repo)
    source = memory_write_record(config, content_markdown="# Source\n\nBase.\n", record_kind="observation", tags=["mcp"])

    observation = _dispatch_tool(
        config,
        "memory_write",
        {
            "operation": "observation",
            "content_markdown": "# Observation\n\nDerived evidence.\n",
            "tags": ["mcp"],
            "derived_from_record_ids": [source["id"]],
        },
    )
    traced = _dispatch_tool(
        config,
        "memory_context",
        {"operation": "trace_lineage", "record_id": observation["id"]},
    )

    assert observation["ok"] is True
    assert traced["ok"] is True
    assert traced["record_id"] == observation["id"]


# ---------------------------------------------------------------------------
# §15.2-B: rewrite_query / narrative facade end-to-end coverage
# Covers the four runner statuses: disabled / unavailable / timeout / ok.
# ---------------------------------------------------------------------------


import json as _json

import pytest

from servers.memory_server import memory_llm_runner as _runner
from servers.memory_server import memory_query_rewrite as _qr_module
from servers.memory_server import memory_compile_views as _compile_views


def _enable_capability(repo: Path, capability: str) -> None:
    cfg_path = repo / ".ai-memory/config.json"
    payload = _json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    llm = payload.setdefault("llm_defaults", {})
    caps = llm.setdefault("capabilities", {})
    caps[capability] = {"enabled": True}
    cfg_path.write_text(_json.dumps(payload), encoding="utf-8")


class _FakeClient:
    """Stand-in for ``LLMClient``; the real one is not invoked because we
    monkeypatch the capability worker (`rewrite_query` /
    `generate_snapshot_narrative`).
    """

    def __init__(self):
        self.config = type("Cfg", (), {"model": "fake-model", "timeout": 30.0,
                                       "max_output_tokens_per_call": 1024})()


def _seed_for_retrieval(config) -> str:
    res = memory_write_record(
        config,
        content_markdown="# Material PBR\n\nMaterial pipeline notes.\n",
        record_kind="note",
        scope="personal",
        status="validated",
        tags=["mcp"],
    )
    return res["id"]


def test_dispatch_rewrite_query_disabled(repo: Path) -> None:
    """Default config keeps query_rewrite disabled → status=disabled,
    variants empty, retrieval still succeeds.
    """
    config = load_config(repo)
    _seed_for_retrieval(config)

    result = _dispatch_tool(
        config,
        "memory_context",
        {
            "operation": "retrieve_context",
            "query": "material pbr",
            "rewrite_query": True,
        },
    )
    assert result["ok"] is True
    qr = result.get("query_rewrite")
    assert qr is not None
    assert qr["status"] == "disabled"
    assert qr["variants"] == []


def test_dispatch_rewrite_query_unavailable(repo: Path, monkeypatch) -> None:
    _enable_capability(repo, "query_rewrite")
    config = load_config(repo)
    _seed_for_retrieval(config)

    from servers.memory_server.memory_llm import LLMConfigError

    def boom(_profile):
        raise LLMConfigError("api key missing")

    monkeypatch.setattr(_runner, "_default_client_factory", boom)

    result = _dispatch_tool(
        config,
        "memory_context",
        {
            "operation": "retrieve_context",
            "query": "material pbr",
            "rewrite_query": True,
        },
    )
    assert result["ok"] is True  # retrieval still works
    qr = result["query_rewrite"]
    assert qr["status"] == "unavailable"
    assert qr["variants"] == []


def test_dispatch_rewrite_query_timeout(repo: Path, monkeypatch) -> None:
    _enable_capability(repo, "query_rewrite")
    config = load_config(repo)
    _seed_for_retrieval(config)

    from servers.memory_server.memory_llm import LLMRequestError

    monkeypatch.setattr(_runner, "_default_client_factory", lambda _p: _FakeClient())

    def slow_call(*_a, **_kw):
        raise LLMRequestError("operation timeout")

    monkeypatch.setattr(_qr_module, "rewrite_query", slow_call)

    result = _dispatch_tool(
        config,
        "memory_context",
        {
            "operation": "retrieve_context",
            "query": "material pbr",
            "rewrite_query": True,
        },
    )
    qr = result["query_rewrite"]
    assert qr["status"] == "timeout"
    assert qr["variants"] == []


def test_dispatch_rewrite_query_ok(repo: Path, monkeypatch) -> None:
    _enable_capability(repo, "query_rewrite")
    config = load_config(repo)
    _seed_for_retrieval(config)

    monkeypatch.setattr(_runner, "_default_client_factory", lambda _p: _FakeClient())

    def ok_call(_client, query, *, max_variants=3, context_hint=None, **_kw):
        return _qr_module.QueryRewriteResult(
            ok=True,
            original=query,
            variants=["material pipeline pbr", "pbr roughness metallic"],
            model="fake-model",
        )

    monkeypatch.setattr(_qr_module, "rewrite_query", ok_call)

    result = _dispatch_tool(
        config,
        "memory_context",
        {
            "operation": "retrieve_context",
            "query": "material pbr",
            "rewrite_query": True,
        },
    )
    qr = result["query_rewrite"]
    assert qr["status"] == "ok"
    assert "material pipeline pbr" in qr["variants"]


# ── narrative facade ────────────────────────────────────────────────


def test_dispatch_narrative_disabled(repo: Path) -> None:
    config = load_config(repo)
    _seed_for_retrieval(config)
    result = _dispatch_tool(
        config,
        "memory_context",
        {
            "operation": "compile",
            "target": "daily_snapshot",
            "narrative": True,
        },
    )
    # The compile call itself must succeed; the narrative envelope (if
    # surfaced under either key) must report a non-ok terminal status
    # because snapshot_narrative defaults to disabled.
    assert result.get("ok") is True
    nar = result.get("narrative") or result.get("snapshot_narrative") or {}
    if nar:
        assert nar.get("status") in {"disabled", "skipped", None}
