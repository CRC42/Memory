from __future__ import annotations

from typing import Any

from .memory_config import MemoryConfig
from .memory_corpus import CompilableRecord, compact_body
from .memory_paths import PathManager


def bullet_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    return str(value)


def render_record(record: CompilableRecord, *, body_mode: str) -> list[str]:
    metadata = record.metadata
    if body_mode == "full":
        tags = [str(tag) for tag in metadata.get("tags", []) if str(tag)]
        lines = [
            f"### {record.title}",
            "",
            f"- id: `{metadata.get('id')}`",
            f"- path: `{record.path}`",
            f"- kind: `{metadata.get('record_kind')}`",
            f"- scope/status: `{metadata.get('scope')}` / `{metadata.get('status')}`",
            f"- author: `{metadata.get('author')}`",
            f"- task_id: `{bullet_value(metadata.get('task_id'))}`",
            f"- branch: `{bullet_value(metadata.get('branch'))}`",
            f"- tags: `{bullet_value(tags)}`",
            "",
            record.body,
            "",
        ]
    else:
        lines = [
            f"### {record.title}",
            "",
            f"- id: `{metadata.get('id')}`",
            f"- source: `{record.path}`",
            f"- status: `{metadata.get('status')}`",
            "",
            compact_body(record),
            "",
        ]
    return lines


def legacy_memory_lines(config: MemoryConfig, *, user: str | None) -> list[str]:
    rel_paths = ["memory-bank/activeContext.md", "memory-bank/progress.md"]
    lines: list[str] = []
    manager = PathManager(config)
    for rel_path in rel_paths:
        try:
            resolved = manager.resolve(rel_path, must_exist=True, must_be_file=True)
        except Exception:
            continue
        try:
            snippet = resolved.read_text(encoding="utf-8", errors="replace").strip().splitlines()[:6]
        except OSError:
            continue
        lines.append(f"### `{rel_path}`")
        lines.append("")
        lines.extend(f"> {line}" for line in snippet if line.strip())
        lines.append("")
    return lines


def render_compile_markdown(
    *,
    config: MemoryConfig,
    target: str,
    records: list[CompilableRecord],
    user: str | None,
    task_id: str | None,
    branch: str | None,
    include_scopes: list[str],
    include_statuses: list[str],
    preferred_tags: list[str],
    body_mode: str,
) -> str:
    title_by_target = {
        "runtime_digest": "Runtime Digest",
        "task_handoff": "Task Handoff",
        "system_digest": "System Digest",
        "publish_queue": "Publish Queue",
    }
    title = title_by_target[target]
    lines = [
        f"# {title}",
        "",
        "> Generated deterministically from Markdown + Front Matter records. This file is a rebuildable view, not truth source.",
        "",
        "## Filters",
        "",
        f"- target: `{target}`",
        f"- user: `{bullet_value(user)}`",
        f"- task_id: `{bullet_value(task_id)}`",
        f"- branch: `{bullet_value(branch)}`",
        f"- include_scopes: `{bullet_value(include_scopes)}`",
        f"- include_statuses: `{bullet_value(include_statuses)}`",
        f"- preferred_tags: `{bullet_value(preferred_tags)}`",
        f"- body_mode: `{body_mode}`",
        "",
        "## Included Records",
        "",
    ]

    if target == "runtime_digest":
        legacy_lines = legacy_memory_lines(config, user=user)
        if legacy_lines:
            lines.extend(["## Legacy Memory Files", ""])
            lines.extend(legacy_lines)
            lines.append("")

    if not records:
        lines.extend(["No records matched the compile filters.", ""])
    else:
        for record in records:
            lines.extend(render_record(record, body_mode=body_mode))

    lines.extend(["## Source References", ""])
    if not records:
        lines.append("- none")
    else:
        for record in records:
            lines.append(f"- `{record.metadata.get('id')}` -> `{record.path}`")
    lines.append("")
    return "\n".join(lines)
