"""Tests for memory_key_documents (P4-C: rebuildable key documents).

Doctrine (README §0 / DesignDoc §2.0 / DEVLOG 2026-04-27):
    - Key documents (`activeContext.md` / `progress.md` / `techContext.md` /
      `systemPatterns.md`) are derived views over raw records.
    - Deterministic renderer must produce a complete, no-LLM document.
    - Header MUST carry `<!-- generated_by=memory-mcp renderer=… ... -->`.
    - Existing files without the marker are archived to
      `memory-bank/archive/manual-edits/<doc>-<timestamp>.md` before overwrite.
    - Failure must NOT damage raw records.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from servers.memory_server.memory_config import load_config
from servers.memory_server.memory_key_documents import (
    KEY_DOCUMENT_KEYS,
    KEY_DOCUMENTS,
    build_generated_header,
    is_generated,
    parse_generated_meta,
    rebuild_key_documents,
    render_deterministic_document,
    select_records_for,
)
from servers.memory_server.memory_records import memory_write_record


@pytest.fixture(autouse=True)
def _disable_llm_by_default(monkeypatch):
    """Make `auto` mode default to deterministic across this whole module
    so we never accidentally call a real LLM provider during CI / local
    runs that happen to have llm_config.local.json configured. Tests that
    need the LLM tier re-monkeypatch ``_maybe_build_llm_client``."""
    from servers.memory_server import memory_key_documents as mkd
    monkeypatch.setattr(
        mkd,
        "_maybe_build_llm_client",
        lambda: (None, {"ok": False, "error": "llm_unavailable", "message": "disabled in test"}),
    )


# ── header utilities ──────────────────────────────────────────────────────


def test_build_generated_header_contains_required_fields() -> None:
    header = build_generated_header(
        renderer="deterministic",
        source_record_ids=["r-1", "r-2"],
        generated_at="2026-04-27T12:00:00+00:00",
        config_hash="abc123",
    )
    assert header.startswith("<!--")
    assert "generated_by=memory-mcp" in header
    assert "renderer=deterministic" in header
    assert "source_record_ids=[r-1,r-2]" in header
    assert "generated_at=2026-04-27T12:00:00+00:00" in header
    assert "config_hash=abc123" in header
    assert header.rstrip().endswith("-->")


def test_is_generated_detects_marker_first_line() -> None:
    text = build_generated_header(
        renderer="deterministic",
        source_record_ids=[],
        generated_at="2026-04-27T00:00:00+00:00",
        config_hash="x",
    ) + "\n# Active Context\n"
    assert is_generated(text) is True


def test_is_generated_rejects_arbitrary_html_comment() -> None:
    assert is_generated("<!-- last overwritten by alice -->\n# Active\n") is False
    assert is_generated("# Active\n") is False
    assert is_generated("") is False


def test_parse_generated_meta_round_trips() -> None:
    header = build_generated_header(
        renderer="llm",
        source_record_ids=["a", "b", "c"],
        generated_at="2026-04-27T12:00:00+00:00",
        config_hash="hash9",
    )
    meta = parse_generated_meta(header + "\n# Title\n")
    assert meta is not None
    assert meta["renderer"] == "llm"
    assert meta["source_record_ids"] == ["a", "b", "c"]
    assert meta["generated_at"] == "2026-04-27T12:00:00+00:00"
    assert meta["config_hash"] == "hash9"


def test_parse_generated_meta_returns_none_for_unmarked() -> None:
    assert parse_generated_meta("# Plain markdown\n") is None


# ── KEY_DOCUMENTS contract ────────────────────────────────────────────────


def test_key_documents_covers_four_required_targets() -> None:
    assert set(KEY_DOCUMENT_KEYS) == {
        "activeContext",
        "progress",
        "techContext",
        "systemPatterns",
    }
    for key in KEY_DOCUMENT_KEYS:
        spec = KEY_DOCUMENTS[key]
        assert spec["rel_path"].startswith("memory-bank/")
        assert spec["title"]
        assert isinstance(spec.get("include_kinds", []), list)


# ── deterministic renderer ───────────────────────────────────────────────


@pytest.fixture()
def populated_repo(tmp_path: Path) -> Path:
    (tmp_path / ".ai-memory").mkdir(parents=True, exist_ok=True)
    config = load_config(tmp_path)
    memory_write_record(
        config,
        content_markdown="# Sprint focus\n\nFinish P4-C deterministic renderer.\n",
        record_kind="note",
        scope="personal",
        author="alice",
        tags=["high_value"],
    )
    memory_write_record(
        config,
        content_markdown="# Spdlog adopted\n\nDecision to switch logging backend.\n",
        record_kind="decision",
        scope="project_shared",
        author="alice",
        tags=["build"],
    )
    memory_write_record(
        config,
        content_markdown="# Coding pattern: Result type\n\nUse Result for all dispatch.\n",
        record_kind="rule_candidate",
        scope="personal",
        author="alice",
        tags=["workflow"],
    )
    return tmp_path


def test_render_deterministic_document_contains_header_and_records(populated_repo: Path) -> None:
    config = load_config(populated_repo)
    text = render_deterministic_document(
        config,
        doc_key="progress",
        user="alice",
        generated_at="2026-04-27T12:00:00+00:00",
    )
    assert is_generated(text)
    meta = parse_generated_meta(text)
    assert meta is not None
    assert meta["renderer"] == "deterministic"
    # title section present
    assert "# Progress" in text
    # at least one record body shows up (doctrine: no fabrication, content from raw)
    assert "Spdlog adopted" in text or "Sprint focus" in text


def test_render_deterministic_no_records_still_produces_valid_doc(tmp_path: Path) -> None:
    (tmp_path / ".ai-memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory-bank").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ai-context").mkdir(parents=True, exist_ok=True)
    config = load_config(tmp_path)
    text = render_deterministic_document(
        config,
        doc_key="techContext",
        user=None,
        generated_at="2026-04-27T00:00:00+00:00",
    )
    assert is_generated(text)
    assert "# Tech Context" in text
    # explicit "no records" marker so the document never looks half-rendered
    assert "no records" in text.lower() or "empty" in text.lower()


def test_select_records_for_returns_empty_when_corpus_empty(tmp_path: Path) -> None:
    (tmp_path / ".ai-memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory-bank").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ai-context").mkdir(parents=True, exist_ok=True)
    config = load_config(tmp_path)
    selected = select_records_for(config, doc_key="progress", user=None)
    assert selected == []


# ── rebuild orchestrator ─────────────────────────────────────────────────


def test_rebuild_writes_all_targets_and_includes_generated_header(populated_repo: Path) -> None:
    config = load_config(populated_repo)
    result = rebuild_key_documents(config, targets=None, user="alice", renderer="deterministic")
    assert result["ok"] is True
    assert set(result["written"].keys()) == set(KEY_DOCUMENT_KEYS)
    for key, info in result["written"].items():
        assert info["ok"] is True
        spec = KEY_DOCUMENTS[key]
        path = populated_repo / spec["rel_path"]
        assert path.exists(), f"{key} not written"
        text = path.read_text(encoding="utf-8")
        assert is_generated(text), f"{key} missing generated_by header"


def test_rebuild_skips_targets_filter(populated_repo: Path) -> None:
    config = load_config(populated_repo)
    result = rebuild_key_documents(config, targets=["progress"], user="alice", renderer="deterministic")
    assert result["ok"] is True
    assert set(result["written"].keys()) == {"progress"}
    other = populated_repo / "memory-bank/techContext.md"
    assert not other.exists() or not is_generated(other.read_text(encoding="utf-8"))


def test_rebuild_archives_pre_existing_manual_edits(populated_repo: Path) -> None:
    target = populated_repo / "memory-bank/progress.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Hand-written progress\n\nlegacy content\n", encoding="utf-8")

    config = load_config(populated_repo)
    result = rebuild_key_documents(config, targets=["progress"], user="alice", renderer="deterministic")
    assert result["ok"] is True

    archive_dir = populated_repo / "memory-bank/archive/manual-edits"
    assert archive_dir.exists()
    archived = list(archive_dir.glob("progress-*.md"))
    assert archived, "manual-edit must be archived before overwrite"
    archived_text = archived[0].read_text(encoding="utf-8")
    assert "Hand-written progress" in archived_text
    assert "legacy content" in archived_text

    new_text = target.read_text(encoding="utf-8")
    assert is_generated(new_text)
    assert "Hand-written progress" not in new_text


def test_rebuild_does_not_archive_when_already_generated(populated_repo: Path) -> None:
    config = load_config(populated_repo)
    rebuild_key_documents(config, targets=["progress"], user="alice", renderer="deterministic")
    archive_dir = populated_repo / "memory-bank/archive/manual-edits"
    before = list(archive_dir.glob("progress-*.md")) if archive_dir.exists() else []
    rebuild_key_documents(config, targets=["progress"], user="alice", renderer="deterministic")
    after = list(archive_dir.glob("progress-*.md")) if archive_dir.exists() else []
    assert len(after) == len(before), "second rebuild must not re-archive a generated file"


def test_rebuild_unknown_target_returns_invalid_input(populated_repo: Path) -> None:
    config = load_config(populated_repo)
    result = rebuild_key_documents(config, targets=["bogus"], user="alice", renderer="deterministic")
    assert result["ok"] is False
    assert result["error"] == "invalid_input"


def test_rebuild_llm_renderer_without_client_returns_llm_unavailable(monkeypatch, populated_repo: Path) -> None:
    """When `renderer='llm'` is forced but no LLM is configured, the call
    must surface ``llm_unavailable`` per-doc rather than silently downgrading.

    In ``renderer='auto'`` mode (covered elsewhere) the orchestrator falls
    back to deterministic instead.
    """
    from servers.memory_server import memory_key_documents as mkd
    monkeypatch.setattr(
        mkd,
        "_maybe_build_llm_client",
        lambda: (None, {"ok": False, "error": "llm_unavailable", "message": "forced for test"}),
    )
    config = load_config(populated_repo)
    result = rebuild_key_documents(config, targets=["progress"], user="alice", renderer="llm")
    assert result["ok"] is False
    err = result["errors"]["progress"]
    assert err["error"] == "llm_unavailable"


def test_rebuild_embedding_renderer_requires_enabled_flag(populated_repo: Path) -> None:
    """Without ``embeddings.enabled=true`` the embedding tier is hard-disabled.

    Once the flag is on, the orchestrator falls through to the deterministic
    tier when the vector index is missing — see
    ``test_rebuild_embedding_renderer_falls_back_to_deterministic`` below.
    """

    config = load_config(populated_repo)
    result = rebuild_key_documents(config, targets=["progress"], user="alice", renderer="embedding")
    assert result["ok"] is False
    assert result["error"] == "embeddings_disabled"


def test_rebuild_dispatch_via_memory_context_op(populated_repo: Path) -> None:
    from servers.memory_server.server import _dispatch_tool

    config = load_config(populated_repo)
    result = _dispatch_tool(
        config,
        "memory_context",
        {"operation": "rebuild_key_documents", "targets": ["activeContext"], "user": "alice"},
    )
    assert result["ok"] is True
    assert "activeContext" in result["written"]


# ── mode gating + LLM tier ───────────────────────────────────────────────


def _set_key_documents_mode(repo: Path, *, mode: str) -> None:
    import json as _json
    cfg_path = repo / ".ai-memory/config.json"
    data = _json.loads(cfg_path.read_text(encoding="utf-8"))
    kd = data.get("key_documents") or {}
    kd["mode"] = mode
    data["key_documents"] = kd
    cfg_path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_rebuild_returns_manual_mode_when_disabled(populated_repo: Path) -> None:
    _set_key_documents_mode(populated_repo, mode="manual")
    config = load_config(populated_repo)
    result = rebuild_key_documents(config, targets=["progress"], user="alice")
    assert result["ok"] is False
    assert result["error"] == "key_documents_manual_mode"
    assert result["mode"] == "manual"
    # untouched
    assert not (populated_repo / "memory-bank/progress.md").exists()


def test_rebuild_returns_manual_mode_when_disabled_value(populated_repo: Path) -> None:
    _set_key_documents_mode(populated_repo, mode="disabled")
    config = load_config(populated_repo)
    result = rebuild_key_documents(config, user="alice")
    assert result["ok"] is False
    assert result["error"] == "key_documents_manual_mode"
    assert result["mode"] == "disabled"


def test_rebuild_auto_falls_back_to_deterministic_when_llm_unavailable(monkeypatch, populated_repo: Path) -> None:
    from servers.memory_server import memory_key_documents as mkd
    monkeypatch.setattr(
        mkd,
        "_maybe_build_llm_client",
        lambda: (None, {"ok": False, "error": "llm_unavailable", "message": "forced for test"}),
    )
    config = load_config(populated_repo)
    result = rebuild_key_documents(
        config, targets=["progress"], user="alice", renderer="auto"
    )
    assert result["ok"] is True
    info = result["written"]["progress"]
    assert info["renderer"] == "deterministic"
    assert "llm" in result["renderer_order"]
    assert "deterministic" in result["renderer_order"]


class _StubLLMClient:
    """Minimal LLMClient stand-in for the LLM renderer happy path."""

    class _Cfg:
        model = "stub-model"
        max_input_tokens_per_call = 8000
        max_output_tokens_per_call = 1024

    config = _Cfg()


def test_render_llm_document_uses_map_reduce_distill(monkeypatch, populated_repo: Path) -> None:
    """LLM renderer happy path: header + title + role come from us; body from LLM."""
    from servers.memory_server import memory_key_documents as mkd

    captured: dict[str, object] = {}

    def fake_map_reduce_distill(client, raw_records, **kwargs):
        captured["records"] = raw_records
        captured["kwargs"] = kwargs
        return {"id": kwargs["record_id"], "content": "## Highlights\n- Sprint focus: P4-C\n- Decision: spdlog\n"}

    monkeypatch.setattr(
        "servers.memory_server.memory_llm_pipeline.map_reduce_distill",
        fake_map_reduce_distill,
    )

    config = load_config(populated_repo)
    text = mkd.render_llm_document(
        config,
        doc_key="progress",
        user="alice",
        llm_client=_StubLLMClient(),
        generated_at="2026-04-27T00:00:00+00:00",
    )
    assert is_generated(text)
    meta = parse_generated_meta(text)
    assert meta is not None
    assert meta["renderer"] == "llm"
    assert "# Progress" in text
    assert "## Highlights" in text
    assert "Sprint focus: P4-C" in text
    # raw record dicts were passed through map_reduce_distill
    assert captured["records"], "expected non-empty records to be distilled"
    for rd in captured["records"]:
        assert rd.get("provenance") == "raw_capture"
        assert rd.get("immutable") is True


def test_rebuild_auto_uses_llm_when_client_available(monkeypatch, populated_repo: Path) -> None:
    """When LLM client is available (stub), auto mode prefers it over deterministic."""
    from servers.memory_server import memory_key_documents as mkd

    monkeypatch.setattr(
        mkd, "_maybe_build_llm_client", lambda: (_StubLLMClient(), None)
    )

    def fake_map_reduce_distill(client, raw_records, **kwargs):
        return {"id": kwargs["record_id"], "content": "## LLM body\n- distilled\n"}

    monkeypatch.setattr(
        "servers.memory_server.memory_llm_pipeline.map_reduce_distill",
        fake_map_reduce_distill,
    )

    config = load_config(populated_repo)
    result = rebuild_key_documents(config, targets=["progress"], user="alice", renderer="auto")
    assert result["ok"] is True
    info = result["written"]["progress"]
    assert info["renderer"] == "llm"

    text = (populated_repo / "memory-bank/progress.md").read_text(encoding="utf-8")
    meta = parse_generated_meta(text)
    assert meta is not None
    assert meta["renderer"] == "llm"
    assert "## LLM body" in text


def test_rebuild_auto_falls_back_when_llm_render_raises(monkeypatch, populated_repo: Path) -> None:
    """If the LLM tier raises mid-render, the orchestrator must rebuild via
    the deterministic tier so the file ends up in a consistent state."""
    from servers.memory_server import memory_key_documents as mkd

    monkeypatch.setattr(
        mkd, "_maybe_build_llm_client", lambda: (_StubLLMClient(), None)
    )

    def boom(*args, **kwargs):
        from servers.memory_server.memory_llm import LLMRequestError
        raise LLMRequestError("simulated provider outage")

    monkeypatch.setattr(
        "servers.memory_server.memory_llm_pipeline.map_reduce_distill",
        boom,
    )

    config = load_config(populated_repo)
    result = rebuild_key_documents(config, targets=["progress"], user="alice", renderer="auto")
    assert result["ok"] is True
    info = result["written"]["progress"]
    assert info["renderer"] == "deterministic"
    text = (populated_repo / "memory-bank/progress.md").read_text(encoding="utf-8")
    meta = parse_generated_meta(text)
    assert meta is not None
    assert meta["renderer"] == "deterministic"


def test_config_parses_key_documents_section(tmp_path: Path) -> None:
    import json as _json
    (tmp_path / ".ai-memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory-bank").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ai-context").mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / ".ai-memory/config.json"
    cfg.write_text(_json.dumps({
        "key_documents": {
            "mode": "manual",
            "renderers": {"prefer_order": ["llm", "deterministic"]},
        }
    }), encoding="utf-8")
    config = load_config(tmp_path)
    assert config.key_documents_mode == "manual"
    assert config.key_documents_prefer_order == ("llm", "deterministic")


def test_config_defaults_when_key_documents_absent(tmp_path: Path) -> None:
    (tmp_path / ".ai-memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory-bank").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ai-context").mkdir(parents=True, exist_ok=True)
    config = load_config(tmp_path)
    assert config.key_documents_mode == "auto"
    assert config.key_documents_prefer_order == ("llm", "deterministic")
