"""Regression tests for P1-A server.py split.

Verifies:
- Public symbols are still importable from `servers.memory_server.server`.
- Default facade exposes exactly 3 tools (memory_read/write/context).
- expose_admin_tools=True adds the legacy/admin set without dropping facades
  and without duplicating `memory_write`.
- Each context operation enum is wired in dispatch.
- create_server returns a Server instance and registers handlers.
"""

from __future__ import annotations

import inspect

from servers.memory_server.memory_config import MemoryConfig
from servers.memory_server import server as server_module
from servers.memory_server.server import (
    SERVER_NAME,
    SERVER_VERSION,
    _BASE_DESCRIPTIONS,
    _build_file_roles,
    _build_tools,
    _check_required,
    _dispatch_memory_context,
    _dispatch_memory_read,
    _dispatch_memory_write,
    _dispatch_tool,
    create_server,
)


def _make_config(tmp_path, expose_admin: bool = False) -> MemoryConfig:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "memory-bank").mkdir()
    (workspace / ".ai-context").mkdir()
    (workspace / ".ai-memory").mkdir()
    cfg_path = workspace / ".ai-memory" / "config.json"
    payload = {"mcp": {"expose_admin_tools": expose_admin}}
    import json

    cfg_path.write_text(json.dumps(payload), encoding="utf-8")
    from servers.memory_server.memory_config import load_config

    return load_config(str(workspace), str(cfg_path))


def test_public_reexports_present():
    assert SERVER_NAME == "generic-memory-mcp"
    assert isinstance(SERVER_VERSION, str) and SERVER_VERSION
    assert "memory_read" in _BASE_DESCRIPTIONS
    assert "memory_write" in _BASE_DESCRIPTIONS
    assert "memory_context" in _BASE_DESCRIPTIONS
    # Tests still import these from the top-level server module.
    for name in (
        "_build_file_roles",
        "_build_tools",
        "_check_required",
        "_dispatch_tool",
        "_dispatch_memory_read",
        "_dispatch_memory_write",
        "_dispatch_memory_context",
        "create_server",
    ):
        assert hasattr(server_module, name), f"missing re-export: {name}"


def test_default_facade_returns_three_tools(tmp_path):
    config = _make_config(tmp_path, expose_admin=False)
    tools = _build_tools(config)
    names = [t.name for t in tools]
    assert names == ["memory_read", "memory_write", "memory_context"]


def test_expose_admin_adds_legacy_without_duplicating_memory_write(tmp_path):
    config = _make_config(tmp_path, expose_admin=True)
    tools = _build_tools(config)
    names = [t.name for t in tools]
    # Facades are still first.
    assert names[:3] == ["memory_read", "memory_write", "memory_context"]
    # No duplicate memory_write.
    assert names.count("memory_write") == 1
    # Sample of expected legacy tools (sanity, not exhaustive).
    expected_legacy = {
        "memory_get",
        "memory_search",
        "memory_guard_check",
        "memory_backup",
        "memory_compact",
        "memory_write_record",
        "memory_rebuild_index",
        "memory_search_records",
        "memory_compile",
        "memory_get_runtime_digest",
        "memory_validate_candidate",
        "memory_publish_candidate",
        "memory_archive_record",
        "memory_update_index",
        "memory_health_check",
        "memory_migrate_records",
        "memory_delete_record",
        "memory_record_observation",
        "memory_link_artifact",
        "memory_trace_lineage",
    }
    assert expected_legacy.issubset(set(names))


def test_check_required_returns_error_for_missing(tmp_path):
    err = _check_required({}, "path")
    assert err is not None
    assert err["error"] == "invalid_input"
    assert "path" in err["message"]
    # None counts as missing.
    err = _check_required({"path": None}, "path")
    assert err is not None
    # Empty string is allowed.
    assert _check_required({"path": ""}, "path") is None


def test_unknown_tool_returns_unknown_tool_error(tmp_path):
    config = _make_config(tmp_path)
    result = _dispatch_tool(config, "memory_does_not_exist", {})
    assert result["ok"] is False
    assert result["error"] == "unknown_tool"


def test_dispatch_memory_read_invalid_operation(tmp_path):
    config = _make_config(tmp_path)
    result = _dispatch_memory_read(config, {"operation": "no_such_op"})
    assert result["ok"] is False
    assert result["error"] == "invalid_input"


def test_dispatch_memory_write_invalid_operation(tmp_path):
    config = _make_config(tmp_path)
    result = _dispatch_memory_write(config, {"operation": "no_such_op"})
    assert result["ok"] is False
    assert result["error"] == "invalid_input"


def test_dispatch_memory_context_invalid_operation(tmp_path):
    config = _make_config(tmp_path)
    result = _dispatch_memory_context(config, {"operation": "no_such_op"})
    assert result["ok"] is False
    assert result["error"] == "invalid_input"


def test_facade_context_operations_match_dispatch(tmp_path):
    """The enum advertised in memory_context schema must be covered by dispatch."""
    config = _make_config(tmp_path)
    tools = _build_tools(config)
    context_tool = next(t for t in tools if t.name == "memory_context")
    op_enum = context_tool.inputSchema["properties"]["operation"]["enum"]

    # Every advertised operation must NOT raise unknown-operation error
    # when called with empty args (the operation itself must be recognized
    # even if it then fails on missing required params).
    for op in op_enum:
        result = _dispatch_memory_context(config, {"operation": op})
        # The dispatch may reject for other reasons (missing params, no records),
        # but it must NOT return the "operation must be one of" message.
        if result.get("ok") is False and result.get("error") == "invalid_input":
            assert "operation must be one of" not in result.get("message", ""), (
                f"operation '{op}' in schema but not in dispatch"
            )


def test_create_server_returns_server_instance(tmp_path):
    config = _make_config(tmp_path)
    srv = create_server(config)
    assert srv is not None
    # MCP Server exposes name attr or similar; do a duck-typed sanity check.
    assert callable(getattr(srv, "create_initialization_options", None))


def test_dispatch_tool_signature_unchanged():
    sig = inspect.signature(_dispatch_tool)
    params = list(sig.parameters)
    assert params == ["config", "name", "args"]
