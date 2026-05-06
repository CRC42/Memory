"""P1-1: bootstrap helpers (v0.6.0 OOTB hardening)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from servers.memory_server.memory_bootstrap import (
    health_green_light,
    merge_mcp_json,
    write_user_setting,
)


def test_write_user_setting_creates_file(tmp_path: Path) -> None:
    result = write_user_setting(tmp_path, "alice")
    assert result["ok"] is True
    assert result["created"] is True

    settings = json.loads((tmp_path / ".vscode" / "settings.json").read_text(encoding="utf-8"))
    assert settings["memory-mcp.userName"] == "alice"


def test_write_user_setting_preserves_existing_keys(tmp_path: Path) -> None:
    settings_path = tmp_path / ".vscode" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"editor.fontSize": 14, "memory-mcp.userName": "old"}),
        encoding="utf-8",
    )

    result = write_user_setting(tmp_path, "bob")
    assert result["ok"] is True
    assert result["created"] is False

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["editor.fontSize"] == 14
    assert settings["memory-mcp.userName"] == "bob"


def test_write_user_setting_rejects_placeholder(tmp_path: Path) -> None:
    result = write_user_setting(tmp_path, "unknown")
    assert result["ok"] is False
    assert result["error"] == "invalid_user"
    assert not (tmp_path / ".vscode" / "settings.json").exists()


def test_merge_mcp_json_creates_entry(tmp_path: Path) -> None:
    result = merge_mcp_json(tmp_path, python_exe="python")
    assert result["ok"] is True

    data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert "memory-mcp" in data["servers"]
    assert data["servers"]["memory-mcp"]["command"] == "python"


def test_merge_mcp_json_writes_env_for_venv_python(tmp_path: Path) -> None:
    memory_root = tmp_path / "MCP" / "Memory"
    venv_python = memory_root / ".venv" / "Scripts" / "python.exe"

    result = merge_mcp_json(tmp_path, python_exe=str(venv_python), memory_root=memory_root)
    assert result["ok"] is True

    data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    entry = data["servers"]["memory-mcp"]
    assert entry["command"] == str(venv_python)
    assert entry["env"] == {
        "PYTHONPATH": str(memory_root).replace("\\", "/"),
        "PYTHONUTF8": "1",
    }


def test_merge_mcp_json_preserves_other_servers(tmp_path: Path) -> None:
    mcp_path = tmp_path / ".vscode" / "mcp.json"
    mcp_path.parent.mkdir(parents=True)
    mcp_path.write_text(
        json.dumps(
            {
                "servers": {"other": {"command": "node", "args": ["x.js"], "type": "stdio"}},
                "inputs": [{"id": "k"}],
            }
        ),
        encoding="utf-8",
    )

    merge_mcp_json(tmp_path, python_exe="C:/py.exe")
    data = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert data["servers"]["other"]["command"] == "node"
    assert data["servers"]["memory-mcp"]["command"] == "C:/py.exe"
    assert data["inputs"] == [{"id": "k"}]


def test_merge_mcp_json_is_idempotent(tmp_path: Path) -> None:
    merge_mcp_json(tmp_path)
    first = (tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8")
    merge_mcp_json(tmp_path)
    second = (tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8")
    assert first == second


def test_health_green_light_fails_without_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)
    (tmp_path / "memory-bank").mkdir()
    (tmp_path / ".ai-memory").mkdir()
    (tmp_path / ".ai-memory" / "config.json").write_text(
        json.dumps({"allowed_roots": ["memory-bank"], "multi_user": {"enabled": True}}),
        encoding="utf-8",
    )

    result = health_green_light(tmp_path)
    assert result["ok"] is False
    user_check = next(c for c in result["checks"] if c["step"] == "validate_user")
    assert user_check["error"] in {"user_not_configured", "user_unknown"}


def test_health_green_light_passes_with_user(tmp_path: Path) -> None:
    (tmp_path / "memory-bank").mkdir()
    (tmp_path / ".ai-memory").mkdir()
    (tmp_path / ".ai-memory" / "config.json").write_text(
        json.dumps({"allowed_roots": ["memory-bank"]}),
        encoding="utf-8",
    )
    write_user_setting(tmp_path, "alice")

    result = health_green_light(tmp_path)
    assert result["ok"] is True
