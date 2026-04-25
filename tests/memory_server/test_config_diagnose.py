"""P1-4: config diagnostics (v0.6.0 OOTB hardening)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from servers.memory_server.memory_config import load_config
from servers.memory_server.memory_diagnose import config_diagnose
from servers.memory_server.server_dispatch import _dispatch_memory_context


def _bootstrap(tmp_path: Path, *, raw: dict | None = None) -> object:
    (tmp_path / "memory-bank").mkdir()
    (tmp_path / ".ai-memory").mkdir()
    payload = raw if raw is not None else {"allowed_roots": ["memory-bank"]}
    (tmp_path / ".ai-memory" / "config.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return load_config(tmp_path)


def test_diagnose_default_sources(tmp_path: Path) -> None:
    config = _bootstrap(tmp_path)
    result = config_diagnose(config)
    assert result["ok"] is True
    fields = result["fields"]
    assert fields["mcp.allow_unknown_user"]["source"] == "default"
    assert fields["mcp.shared_overwrite_policy"]["source"] == "default"
    assert fields["mcp.shared_overwrite_policy"]["value"] == "reject"
    assert fields["mcp.auto_maintenance.enabled"]["source"] == "default"


def test_diagnose_file_source(tmp_path: Path) -> None:
    config = _bootstrap(
        tmp_path,
        raw={
            "allowed_roots": ["memory-bank"],
            "mcp": {
                "allow_unknown_user": True,
                "shared_overwrite_policy": "downgrade",
                "auto_maintenance": {"enabled": False},
            },
        },
    )
    fields = config_diagnose(config)["fields"]
    assert fields["mcp.allow_unknown_user"]["source"] == "file"
    assert fields["mcp.allow_unknown_user"]["value"] is True
    assert fields["mcp.shared_overwrite_policy"]["source"] == "file"
    assert fields["mcp.shared_overwrite_policy"]["value"] == "downgrade"
    assert fields["mcp.auto_maintenance.enabled"]["source"] == "file"
    assert fields["mcp.auto_maintenance.enabled"]["value"] is False


def test_diagnose_user_vscode_source(tmp_path: Path) -> None:
    config = _bootstrap(tmp_path)
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "settings.json").write_text(
        json.dumps({"memory-mcp.userName": "alice"}), encoding="utf-8"
    )
    fields = config_diagnose(config)["fields"]
    assert fields["user.effective"]["source"] == "vscode"
    assert fields["user.effective"]["value"] == "alice"


def test_diagnose_user_env_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _bootstrap(tmp_path)
    monkeypatch.setenv("USERNAME", "bob")
    fields = config_diagnose(config)["fields"]
    assert fields["user.effective"]["source"] == "env"
    assert fields["user.effective"]["value"] == "bob"


def test_dispatch_routes_config_diagnose(tmp_path: Path) -> None:
    config = _bootstrap(tmp_path)
    result = _dispatch_memory_context(config, {"operation": "config_diagnose"})
    assert result["ok"] is True
    assert "fields" in result
