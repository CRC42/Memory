"""P0-1: user id strict validation (v0.6.0 OOTB hardening).

Scope:
- ``is_placeholder_user`` rejects empty / whitespace / literal "unknown"
  variants and names containing path-injection characters.
- ``is_ambiguous_user`` flags common shared admin names (warning, not block).
- ``validate_effective_user`` returns structured error when placeholder
  detected and config does not opt out via ``mcp.allow_unknown_user``.
- Facade entry points (``memory_write`` / ``memory_write_record``) refuse
  to write when validation fails; the failure surfaces as
  ``error="user_not_configured"`` with a ``setup_hint``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from servers.memory_server.memory_config import load_config
from servers.memory_server.memory_users import (
    is_ambiguous_user,
    is_placeholder_user,
    validate_effective_user,
)
from servers.memory_server.memory_writer import memory_write


# ---------------------------------------------------------------------------
# pure-function level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["", " ", "\t", "unknown", "Unknown", "UNKNOWN", "a/b", "a\\b", "a:b", "a\nb", "a\rb", "a\0b"],
)
def test_is_placeholder_user_rejects_invalid(name: str) -> None:
    assert is_placeholder_user(name) is True


@pytest.mark.parametrize("name", ["tiany", "alice", "bob123", "user.surname", "中文用户", "dev-1"])
def test_is_placeholder_user_accepts_valid(name: str) -> None:
    assert is_placeholder_user(name) is False


@pytest.mark.parametrize("name", ["Administrator", "User", "admin", "root", "guest", "default"])
def test_is_ambiguous_user_flags_common_shared_names(name: str) -> None:
    assert is_ambiguous_user(name) is True
    # ambiguous != placeholder; ambiguous still passes hard validation.
    assert is_placeholder_user(name) is False


@pytest.mark.parametrize("name", ["tiany", "alice"])
def test_is_ambiguous_user_passes_personal_names(name: str) -> None:
    assert is_ambiguous_user(name) is False


# ---------------------------------------------------------------------------
# config-level
# ---------------------------------------------------------------------------


def _bootstrap_config(tmp_path: Path, mcp_overrides: dict | None = None) -> object:
    (tmp_path / "memory-bank").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ai-memory").mkdir(parents=True, exist_ok=True)
    cfg = {"allowed_roots": ["memory-bank"]}
    if mcp_overrides is not None:
        cfg["mcp"] = mcp_overrides
    (tmp_path / ".ai-memory" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return load_config(tmp_path)


def test_validate_effective_user_blocks_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)
    config = _bootstrap_config(tmp_path)

    err = validate_effective_user(config)
    assert err is not None
    assert err["error"] == "user_not_configured"
    assert "setup_hint" in err
    assert "memory-mcp.userName" in err["setup_hint"]


def test_validate_effective_user_accepts_real_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERNAME", "alice")
    config = _bootstrap_config(tmp_path)

    assert validate_effective_user(config) is None


def test_validate_effective_user_allows_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)
    config = _bootstrap_config(tmp_path, mcp_overrides={"allow_unknown_user": True})

    assert validate_effective_user(config) is None


def test_validate_effective_user_warns_on_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USERNAME", "Administrator")
    config = _bootstrap_config(tmp_path)

    err = validate_effective_user(config)
    # ambiguous must NOT block; result is None or carries a warning marker.
    assert err is None or err.get("warning") == "user_ambiguous"


# ---------------------------------------------------------------------------
# facade-level
# ---------------------------------------------------------------------------


def test_memory_write_blocked_when_user_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)
    config = _bootstrap_config(tmp_path)

    result = memory_write(config, path="memory-bank/note.md", content="# hi\nbody\n")
    assert result.get("ok") is False
    assert result.get("error") == "user_not_configured"
    assert "setup_hint" in result
    # No file written.
    assert not (tmp_path / "memory-bank" / "note.md").exists()


def test_memory_write_succeeds_with_real_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USERNAME", "alice")
    config = _bootstrap_config(tmp_path)

    result = memory_write(config, path="memory-bank/note.md", content="# hi\nbody\n")
    assert result.get("ok") is True
    assert (tmp_path / "memory-bank" / "note.md").exists()
