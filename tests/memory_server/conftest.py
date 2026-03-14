from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[4]
MEMORY_ROOT = PROJECT_ROOT / "MCP" / "memory"
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _write(
        tmp_path / "memory-bank/notes.md",
        "# Boss Notes\nline2\nline3\nline4\n",
    )
    _write(
        tmp_path / "memory-bank/activeContext.md",
        "# Active\n## Current sprint\n- sprint A\n",
    )
    _write(
        tmp_path / "memory-bank/long.md",
        "x" * 60,
    )
    _write(
        tmp_path / ".ai-context/current-task.md",
        "\n".join(
            [
                "# Current Task",
                "## Task",
                "- build MVP",
                "## Goal / Done Definition",
                "- tests passing",
                "## Current status",
                "- in progress",
                "## Relevant files / assets",
                "- memory-bank/notes.md",
                "## Constraints",
                "- phase1 only",
                "## Latest attempts",
                "- attempt 1",
                "- attempt 2",
                "- attempt 3",
                "- attempt 4",
                "- attempt 5",
                "## What I want from AI right now",
                "- produce safe compact output",
                "",
            ]
        ),
    )
    _write(
        tmp_path / ".ai-context/latest-error.md",
        "\n".join(
            [
                "# Error Log",
                "## Symptom",
                "- crash on open",
                "## When it happens",
                "- during startup",
                "## First meaningful error",
                "- E001 invalid state",
                "## What changed",
                "- upgraded plugin",
                "## AI request",
                "- help isolate issue",
                "",
            ]
        ),
    )

    (tmp_path / ".ai-memory/backups").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ai-memory/temp").mkdir(parents=True, exist_ok=True)
    _write(tmp_path / ".ai-memory/events.jsonl", "")

    config_data = {
        "allowed_roots": [".ai-context", "memory-bank"],
        "excluded_dirs": ["Binaries", "Intermediate", "DerivedDataCache", "Saved/Cooked"],
        "max_file_size_bytes": 1048576,
        "skip_binary_files": True,
        "events_file": ".ai-memory/events.jsonl",
        "backups_dir": ".ai-memory/backups",
        "temp_dir": ".ai-memory/temp",
        "backup": {
            "max_total_bytes": 524288,
            "max_file_bytes": 262144,
            "max_batches": 5,
        },
        "guard": {
            "default_max_chars": 50,
            "default_max_tokens": 20,
            "total_max_chars": 5000,
            "total_max_tokens": 2000,
            "targets": [
                {"path": "memory-bank/long.md", "max_chars": 20, "policy": "warm_context", "role": "test long file"},
                {"path": ".ai-context/current-task.md", "max_chars": 1000, "policy": "hot_task", "role": "hot task context"},
                {"path": "memory-bank/notes.md", "max_chars": 500, "role": "notes"},
                {"path": "memory-bank/activeContext.md", "max_chars": 500, "role": "sprint focus"},
            ],
        },
    }
    _write(tmp_path / ".ai-memory/config.json", json.dumps(config_data, ensure_ascii=False, indent=2))
    return tmp_path
