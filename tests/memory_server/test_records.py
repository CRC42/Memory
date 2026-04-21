from __future__ import annotations

from pathlib import Path

from servers.memory_server.memory_config import load_config
from servers.memory_server.memory_records import memory_write_record, parse_record_markdown
from servers.memory_server.server import _dispatch_tool


def test_write_record_creates_candidate_markdown_with_front_matter(repo: Path) -> None:
    config = load_config(repo)

    result = memory_write_record(
        config,
        content_markdown="# Export Size Rule\n\nKeep `max_texture_size` during export.\n",
        record_kind="rule_candidate",
        scope="personal",
        author="yangskin",
        tags=["asset_pipeline", "texture", "validation"],
        confidence=0.82,
        source_refs=["evt_1023"],
        task_id="task_sp_sync",
        branch="feature/sp-roundtrip",
    )

    assert result["ok"] is True
    assert result["path"].startswith("memory-bank/candidates/")
    assert result["path"].endswith(".md")

    record_path = repo / result["path"]
    metadata, body = parse_record_markdown(record_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "1.0"
    assert metadata["id"] == result["id"]
    assert metadata["record_kind"] == "rule_candidate"
    assert metadata["scope"] == "personal"
    assert metadata["status"] == "candidate"
    assert metadata["author"] == "yangskin"
    assert metadata["tags"] == ["asset_pipeline", "texture", "validation"]
    assert metadata["confidence"] == 0.82
    assert metadata["source_refs"] == ["evt_1023"]
    assert metadata["task_id"] == "task_sp_sync"
    assert metadata["branch"] == "feature/sp-roundtrip"
    assert body.startswith("# Export Size Rule")


def test_write_record_rejects_unknown_record_kind(repo: Path) -> None:
    config = load_config(repo)

    result = memory_write_record(
        config,
        content_markdown="# Bad\n",
        record_kind="random_thought",
        tags=["mcp"],
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_input"
    assert "record_kind" in result["message"]


def test_write_record_rejects_uncontrolled_tag(repo: Path) -> None:
    config = load_config(repo)

    result = memory_write_record(
        config,
        content_markdown="# Bad\n",
        record_kind="note",
        tags=["invented_tag"],
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_input"
    assert "tags" in result["message"]


def test_dispatch_write_record(repo: Path) -> None:
    config = load_config(repo)

    result = _dispatch_tool(
        config,
        "memory_write_record",
        {
            "content_markdown": "# Handoff\n\nContinue record-layer work.\n",
            "record_kind": "handoff",
            "scope": "personal",
            "tags": ["handoff_ready", "mcp"],
        },
    )

    assert result["ok"] is True
    assert result["path"].startswith("memory-bank/people/")
    assert (repo / result["path"]).is_file()
