"""Rebuildable key documents (P4-C — 无感最高原则落地).

Doctrine (README §0 / DesignDoc §2.0 / DEVLOG 2026-04-27):

* The four ``memory-bank/{activeContext, progress, techContext,
  systemPatterns}.md`` files are *derived views* over raw records.
* Their canonical body is reconstructed from the raw corpus on demand;
  humans never edit them directly. Manual edits, when found, are
  preserved by archival (see ``archive_manual_edit``) but the rebuilt
  view always overwrites the in-place file so the next read is
  consistent with the raw substrate.
* Renderers degrade in three tiers: LLM (preferred) → embedding-based
  template → deterministic. Only the deterministic tier is implemented
  in this first slice; the public surface accepts the renderer name
  ahead of time so callers can opt-in once LLM/embedding tiers land.
* Every generated body carries a ``<!-- generated_by=memory-mcp ... -->``
  header on the first line. ``is_generated`` and ``parse_generated_meta``
  let other tools (compactor, snapshot review, governance) tell raw
  text apart from rebuilt text without re-parsing the body.

Public API:
    KEY_DOCUMENTS, KEY_DOCUMENT_KEYS
    build_generated_header / is_generated / parse_generated_meta
    select_records_for / render_deterministic_document
    rebuild_key_documents

This module deliberately bypasses ``memory_writer.memory_write`` because
the writer applies ``user_scoped`` redirection and ``append_only``
downgrade for these very paths — both of which are explicitly *wrong*
for a derived view that must overwrite the canonical file in place.
Safety primitives (``file_lock``, ``backup_files``, ``_atomic_write_text``,
``append_event``) are still reused so the rebuild path stays crash-safe
and auditable.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .memory_backup import backup_files
from .memory_config import MemoryConfig
from .memory_corpus import CompilableRecord, iter_compilable_records
from .memory_events import append_event, get_current_user
from .memory_locks import LockTimeoutError, file_lock
from .memory_record_io import DiskFullError, _atomic_write_text
from .memory_request_id import new_request_id
from .memory_result import error_result


# ── Document specifications ──────────────────────────────────────────────


KEY_DOCUMENTS: dict[str, dict[str, Any]] = {
    "activeContext": {
        "rel_path": "memory-bank/activeContext.md",
        "title": "Active Context",
        "role": "current sprint focus, recent decisions, in-progress items",
        "include_kinds": ["note", "decision", "observation", "incident"],
        "preferred_tags": ["high_value", "handoff_ready", "needs_validation"],
        "max_items": 30,
    },
    "progress": {
        "rel_path": "memory-bank/progress.md",
        "title": "Progress",
        "role": "feature completion status, milestones, completed deliverables",
        "include_kinds": ["note", "decision", "observation", "validation_result"],
        "preferred_tags": ["high_value", "build", "asset_pipeline", "validation"],
        "max_items": 60,
    },
    "techContext": {
        "rel_path": "memory-bank/techContext.md",
        "title": "Tech Context",
        "role": "tech stack, plugin matrix, architecture configuration",
        "include_kinds": [
            "decision",
            "claim_candidate",
            "rule_candidate",
            "system_rule",
            "note",
        ],
        "preferred_tags": ["build", "asset_pipeline", "mcp"],
        "max_items": 60,
    },
    "systemPatterns": {
        "rel_path": "memory-bank/systemPatterns.md",
        "title": "System Patterns",
        "role": "architecture patterns, coding conventions, design decisions",
        "include_kinds": [
            "decision",
            "rule_candidate",
            "system_rule",
            "claim_candidate",
            "procedure",
        ],
        "preferred_tags": ["workflow", "validation", "mcp"],
        "max_items": 60,
    },
}

KEY_DOCUMENT_KEYS: tuple[str, ...] = tuple(KEY_DOCUMENTS.keys())

ARCHIVE_DIR_RELPATH = "memory-bank/archive/manual-edits"

_GENERATED_MARKER = "generated_by=memory-mcp"
_HEADER_RE = re.compile(
    r"^<!--\s*generated_by=memory-mcp\s+(?P<body>.+?)\s*-->\s*$"
)


# ── Header utilities ─────────────────────────────────────────────────────


def build_generated_header(
    *,
    renderer: str,
    source_record_ids: Iterable[str],
    generated_at: str,
    config_hash: str,
) -> str:
    ids = ",".join(str(x) for x in source_record_ids)
    return (
        f"<!-- generated_by=memory-mcp"
        f" renderer={renderer}"
        f" source_record_ids=[{ids}]"
        f" generated_at={generated_at}"
        f" config_hash={config_hash} -->"
    )


def is_generated(text: str) -> bool:
    if not text:
        return False
    first = text.lstrip().splitlines()[0] if text.lstrip().splitlines() else ""
    return _GENERATED_MARKER in first and first.strip().startswith("<!--")


def parse_generated_meta(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    lines = text.lstrip().splitlines()
    if not lines:
        return None
    match = _HEADER_RE.match(lines[0].strip())
    if not match:
        return None
    body = match.group("body")
    out: dict[str, Any] = {}
    # tokens are space-separated `key=value` pairs; the value for
    # source_record_ids is `[id1,id2]` so we tokenise carefully.
    for token in _split_header_tokens(body):
        if "=" not in token:
            continue
        key, raw = token.split("=", 1)
        if key == "source_record_ids":
            inner = raw.strip()
            if inner.startswith("[") and inner.endswith("]"):
                inner = inner[1:-1]
            out[key] = [piece for piece in inner.split(",") if piece]
        else:
            out[key] = raw
    return out


def _split_header_tokens(body: str) -> list[str]:
    tokens: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch.isspace() and depth == 0:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


# ── Record selection ────────────────────────────────────────────────────


def _record_created_at(record: CompilableRecord) -> str:
    return str(record.metadata.get("created_at") or "")


def _record_kind(record: CompilableRecord) -> str:
    return str(record.metadata.get("record_kind") or record.metadata.get("kind") or "")


def _record_id(record: CompilableRecord) -> str:
    return str(record.metadata.get("id") or "")


def _record_tags(record: CompilableRecord) -> list[str]:
    raw = record.metadata.get("tags") or []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    return []


def select_records_for(
    config: MemoryConfig,
    *,
    doc_key: str,
    user: str | None,
) -> list[CompilableRecord]:
    spec = KEY_DOCUMENTS[doc_key]
    include_kinds = set(spec.get("include_kinds") or [])
    preferred_tags = set(spec.get("preferred_tags") or [])
    max_items = int(spec.get("max_items") or 60)

    records, _ = iter_compilable_records(config)
    selected: list[CompilableRecord] = []
    for rec in records:
        kind = _record_kind(rec)
        if include_kinds and kind not in include_kinds:
            continue
        if doc_key == "activeContext" and user:
            author = str(rec.metadata.get("author") or "")
            # activeContext favours the asking user but does not exclude
            # other authors entirely — they sort lower instead.
            rec_user_match = (author == user)
        else:
            rec_user_match = True

        score = 0
        tags = set(_record_tags(rec))
        if preferred_tags & tags:
            score += 10
        if rec_user_match:
            score += 5
        # newer first
        rec_sort = (-score, _record_created_at(rec))
        selected.append((rec_sort, rec))  # type: ignore[arg-type]

    selected.sort(key=lambda pair: pair[0], reverse=True)
    return [rec for _, rec in selected[:max_items]]


# ── Renderer ────────────────────────────────────────────────────────────


def _config_hash_for(config: MemoryConfig, doc_key: str) -> str:
    spec = KEY_DOCUMENTS[doc_key]
    payload = repr((doc_key, spec["rel_path"], spec.get("include_kinds"), spec.get("preferred_tags"), spec.get("max_items")))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def render_deterministic_document(
    config: MemoryConfig,
    *,
    doc_key: str,
    user: str | None,
    generated_at: str | None = None,
) -> str:
    if doc_key not in KEY_DOCUMENTS:
        raise KeyError(doc_key)
    spec = KEY_DOCUMENTS[doc_key]
    records = select_records_for(config, doc_key=doc_key, user=user)
    record_ids = [_record_id(r) for r in records if _record_id(r)]
    header = build_generated_header(
        renderer="deterministic",
        source_record_ids=record_ids,
        generated_at=generated_at or _now_iso(),
        config_hash=_config_hash_for(config, doc_key),
    )

    lines: list[str] = [header, "", f"# {spec['title']}", ""]
    role = spec.get("role")
    if role:
        lines.append(f"> _{role}_")
        lines.append("")

    if not records:
        lines.append("_No raw records currently match this view (corpus is empty)._")
        lines.append("")
        return "\n".join(lines) + "\n"

    for rec in records:
        title = rec.title or _record_id(rec) or "(untitled)"
        kind = _record_kind(rec) or "record"
        rid = _record_id(rec)
        created = _record_created_at(rec)
        tags = _record_tags(rec)
        meta_bits = [f"kind=`{kind}`"]
        if rid:
            meta_bits.append(f"id=`{rid}`")
        if created:
            meta_bits.append(f"created=`{created}`")
        if tags:
            meta_bits.append("tags=" + ",".join(f"`{t}`" for t in tags))

        lines.append(f"## {title}")
        lines.append("")
        lines.append("> " + " · ".join(meta_bits))
        lines.append("")
        body = rec.body.strip()
        # strip the title heading from body to avoid duplication
        body_lines = body.splitlines()
        if body_lines and body_lines[0].strip().lstrip("#").strip() == title.strip():
            body = "\n".join(body_lines[1:]).strip()
        if body:
            lines.append(body)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── Manual-edit archival + rebuild orchestrator ──────────────────────────


def _archive_manual_edit(
    config: MemoryConfig,
    rel_path: str,
    current_text: str,
    *,
    timestamp: str,
) -> str:
    archive_dir = config.repo_root / ARCHIVE_DIR_RELPATH
    archive_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(rel_path).stem
    safe_ts = timestamp.replace(":", "").replace("-", "").replace("+", "").replace(".", "")
    archive_path = archive_dir / f"{stem}-{safe_ts}.md"
    # ensure unique
    counter = 0
    while archive_path.exists():
        counter += 1
        archive_path = archive_dir / f"{stem}-{safe_ts}-{counter}.md"
    notice = (
        f"<!-- archived manual-edit of {rel_path} at {timestamp} by memory-mcp rebuild -->\n"
    )
    archive_path.write_text(notice + current_text, encoding="utf-8")
    return str(archive_path.relative_to(config.repo_root).as_posix())


def render_llm_document(
    config: MemoryConfig,
    *,
    doc_key: str,
    user: str | None,
    llm_client: Any,
    generated_at: str | None = None,
) -> str:
    """LLM-backed renderer: produces the same scaffold as the deterministic
    tier (header → title → role) but the body is a faithful, concise summary
    drafted by the LLM over the selected raw records.

    Schema invariants are preserved by deterministic code:
    - The ``<!-- generated_by=memory-mcp ... -->`` header always comes from
      :func:`build_generated_header` (LLM never writes it).
    - Title and role lines are stamped by us, not the LLM.
    - The LLM only fills the body region after the role blockquote.

    Raises ``KeyError`` for unknown ``doc_key``. Any LLM-side failure
    (config / network / empty output) propagates as ``LLMError`` so the
    orchestrator can fall back to the deterministic tier.
    """
    if doc_key not in KEY_DOCUMENTS:
        raise KeyError(doc_key)
    spec = KEY_DOCUMENTS[doc_key]
    records = select_records_for(config, doc_key=doc_key, user=user)

    # Local imports keep memory_llm optional at module load time.
    from .memory_llm import (
        DEFAULT_DISTILL_SYSTEM_PROMPT,
        make_raw_record,
    )
    from .memory_llm_pipeline import map_reduce_distill

    raw_dicts: list[dict[str, Any]] = []
    record_ids: list[str] = []
    for rec in records:
        rid = _record_id(rec) or f"anon::{len(raw_dicts)}"
        body = rec.body.strip() or rec.title or ""
        if not body:
            continue
        raw_dicts.append(
            make_raw_record(
                record_id=rid,
                content=body,
                source=rec.path or "memory_key_documents",
                captured_at=_record_created_at(rec) or _now_iso(),
                author=str(rec.metadata.get("author") or "system"),
                extra_meta={
                    "record_kind": _record_kind(rec),
                    "tags": _record_tags(rec),
                    "title": rec.title,
                },
            )
        )
        if _record_id(rec):
            record_ids.append(_record_id(rec))

    title = spec["title"]
    role = spec.get("role") or ""
    header = build_generated_header(
        renderer="llm",
        source_record_ids=record_ids,
        generated_at=generated_at or _now_iso(),
        config_hash=_config_hash_for(config, doc_key),
    )

    if not raw_dicts:
        # No corpus → produce the same "empty" scaffold as deterministic
        # so the schema stays consistent and we don't fabricate content.
        lines = [header, "", f"# {title}", ""]
        if role:
            lines.append(f"> _{role}_")
            lines.append("")
        lines.append("_No raw records currently match this view (corpus is empty)._")
        return "\n".join(lines) + "\n"

    sys_prompt = (
        f"{DEFAULT_DISTILL_SYSTEM_PROMPT}\n\n"
        f"Compose the body of the project's '{title}' document. "
        f"Role of this document: {role}. "
        "Output GitHub-flavored Markdown only. Do not write a top-level title "
        "(no `# {title}` line) — only sub-headings (## …) and prose. "
        "Stay strictly grounded in the raw records above; do not invent facts, "
        "deadlines, owners, file paths, or status. Prefer concise sectioned bullets."
    )
    user_instruction = (
        f"Produce the body of '{title}'. "
        "Group related raw records under short ## sub-headings. "
        "If a record contradicts another, surface both rather than picking one."
    )

    distilled = map_reduce_distill(
        llm_client,
        raw_dicts,
        record_id=f"key_documents::{doc_key}",
        distilled_at=generated_at or _now_iso(),
        system_prompt=sys_prompt,
        user_instruction=user_instruction,
        kind="summary",
    )
    body = str(distilled.get("content") or "").strip()
    if not body:
        from .memory_llm import LLMRequestError
        raise LLMRequestError(f"LLM returned empty body for key document {doc_key!r}")

    lines = [header, "", f"# {title}", ""]
    if role:
        lines.append(f"> _{role}_")
        lines.append("")
    lines.append(body)
    return "\n".join(lines).rstrip() + "\n"


def _rebuild_one(
    config: MemoryConfig,
    *,
    doc_key: str,
    user: str | None,
    request_id: str,
    tier: str = "deterministic",
    llm_client: Any = None,
) -> dict[str, Any]:
    spec = KEY_DOCUMENTS[doc_key]
    rel_path = spec["rel_path"]
    target = (config.repo_root / rel_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    generated_at = _now_iso()
    try:
        if tier == "llm":
            if llm_client is None:
                return error_result(
                    "llm_unavailable",
                    "LLM tier selected but no client available",
                    path=rel_path,
                )
            rendered = render_llm_document(
                config,
                doc_key=doc_key,
                user=user,
                llm_client=llm_client,
                generated_at=generated_at,
            )
            renderer_used = "llm"
        else:
            rendered = render_deterministic_document(
                config, doc_key=doc_key, user=user, generated_at=generated_at
            )
            renderer_used = "deterministic"
    except Exception as exc:
        # Surface so the orchestrator can try the next tier
        return error_result(
            "render_failed",
            f"{tier} renderer failed for {doc_key!r}: {type(exc).__name__}: {exc}",
            path=rel_path,
            tier=tier,
        )

    archived_to: str | None = None
    try:
        with file_lock(config.repo_root, target):
            if target.exists() and target.is_file():
                try:
                    existing = target.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    return error_result(
                        "read_failed",
                        f"failed to read existing {rel_path}: {exc}",
                        path=rel_path,
                    )
                if existing.strip() and not is_generated(existing):
                    archived_to = _archive_manual_edit(
                        config, rel_path, existing, timestamp=generated_at
                    )
                # routine pre-write backup so we can roll back the rebuild
                backup_files(
                    config,
                    [rel_path],
                    reason=f"key_documents.rebuild {doc_key} ({renderer_used})",
                    tag="pre_rebuild",
                    event_type="memory_backup",
                    write_event=True,
                )
            try:
                _atomic_write_text(
                    target, rendered, fsync_strict=config.mcp_fsync_strict
                )
            except DiskFullError as exc:
                return error_result(
                    "disk_full",
                    f"out of disk space writing {rel_path}: {exc}",
                    errno=exc.errno,
                    path=rel_path,
                )
            except OSError as exc:
                return error_result(
                    "write_failed",
                    f"failed to write {rel_path}: {exc}",
                    path=rel_path,
                )
    except LockTimeoutError as exc:
        return error_result(
            "lock_timeout",
            f"could not acquire write lock for {rel_path}: {exc}",
            path=rel_path,
        )

    append_event(
        config,
        event_type="key_document_rebuilt",
        payload={
            "doc_key": doc_key,
            "path": rel_path,
            "renderer": renderer_used,
            "generated_at": generated_at,
            "archived_manual_edit_to": archived_to,
            "request_id": request_id,
            "user": get_current_user(config.repo_root),
            "for_user": user,
        },
    )

    return {
        "ok": True,
        "doc_key": doc_key,
        "path": rel_path,
        "renderer": renderer_used,
        "generated_at": generated_at,
        "archived_manual_edit_to": archived_to,
    }


def rebuild_key_documents(
    config: MemoryConfig,
    *,
    targets: list[str] | None = None,
    user: str | None = None,
    renderer: str = "auto",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Rebuild one or more key documents from raw records.

    Args:
        config: Active MemoryConfig.
        targets: Subset of ``KEY_DOCUMENT_KEYS``. ``None`` = rebuild all.
        user: Optional asking user — only influences ranking for
            ``activeContext`` (records by this user score higher).
        renderer: Renderer selection.
            - ``"auto"`` (default): walk ``config.key_documents_prefer_order``
              and use the first tier that succeeds (typically ``llm`` →
              ``deterministic``). Per-document errors fall back to the next
              tier transparently.
            - ``"deterministic"``: force the no-LLM template renderer.
            - ``"llm"``: force the LLM renderer; if the LLM client is
              unavailable the call returns ``error="llm_unavailable"``.
            - ``"embedding"``: reserved for the future RAG-backed renderer
              (P5); currently returns ``error="not_implemented"``.

    Returns:
        ``{ok, written: {doc_key: per_doc_result}, errors: {…}, mode,
        renderer, request_id}``. When ``key_documents.mode`` is
        ``"manual"`` or ``"disabled"`` the call returns
        ``error="key_documents_manual_mode"`` without touching disk.
    """
    rid = request_id or new_request_id()

    mode = getattr(config, "key_documents_mode", "auto")
    if mode != "auto":
        return {
            "ok": False,
            "error": "key_documents_manual_mode",
            "message": (
                f"key_documents.mode={mode!r}: rebuild is disabled. "
                "Set key_documents.mode='auto' in .ai-memory/config.json to enable."
            ),
            "mode": mode,
            "request_id": rid,
        }

    if renderer == "embedding":
        return {
            "ok": False,
            "error": "not_implemented",
            "message": (
                "renderer='embedding' is reserved for the future RAG-backed "
                "tier (P5); use 'deterministic', 'llm', or 'auto'."
            ),
            "request_id": rid,
        }
    if renderer not in {"deterministic", "auto", "llm"}:
        return error_result(
            "invalid_input",
            f"renderer must be one of: auto, deterministic, llm, embedding (got {renderer!r})",
            request_id=rid,
        )

    if targets is None:
        chosen = list(KEY_DOCUMENT_KEYS)
    else:
        if not isinstance(targets, list) or not targets:
            return error_result("invalid_input", "targets must be a non-empty list", request_id=rid)
        unknown = [t for t in targets if t not in KEY_DOCUMENTS]
        if unknown:
            return error_result(
                "invalid_input",
                f"unknown targets: {sorted(unknown)}; valid keys: {list(KEY_DOCUMENT_KEYS)}",
                request_id=rid,
            )
        chosen = list(targets)

    # Compose per-doc renderer order
    if renderer == "deterministic":
        per_doc_order: tuple[str, ...] = ("deterministic",)
    elif renderer == "llm":
        per_doc_order = ("llm",)
    else:  # auto
        per_doc_order = tuple(
            r for r in getattr(config, "key_documents_prefer_order", ("llm", "deterministic"))
            if r in {"llm", "deterministic"}
        ) or ("deterministic",)
        if "deterministic" not in per_doc_order:
            per_doc_order = per_doc_order + ("deterministic",)

    # Lazily build LLM client only when needed
    llm_client = None
    llm_unavailable_err: dict[str, Any] | None = None
    if "llm" in per_doc_order:
        llm_client, llm_unavailable_err = _maybe_build_llm_client()

    written: dict[str, dict[str, Any]] = {}
    errors: dict[str, dict[str, Any]] = {}
    for doc_key in chosen:
        last_error: dict[str, Any] | None = None
        for tier in per_doc_order:
            if tier == "llm" and llm_client is None:
                # explicit llm-only request → surface error; auto-mode falls through
                if renderer == "llm":
                    last_error = llm_unavailable_err or error_result(
                        "llm_unavailable", "LLM client unavailable"
                    )
                    break
                continue
            outcome = _rebuild_one(
                config,
                doc_key=doc_key,
                user=user,
                request_id=rid,
                tier=tier,
                llm_client=llm_client,
            )
            if outcome.get("ok"):
                last_error = None
                written[doc_key] = outcome
                break
            last_error = outcome
            # tier failed; try next tier (if any)
        if doc_key not in written and last_error is not None:
            errors[doc_key] = last_error

    return {
        "ok": not errors,
        "written": written,
        "errors": errors,
        "mode": mode,
        "renderer": renderer,
        "renderer_order": list(per_doc_order),
        "request_id": rid,
    }


def _maybe_build_llm_client() -> tuple[Any, dict[str, Any] | None]:
    """Best-effort LLMClient construction; returns (client_or_None, err_or_None)."""
    try:
        from .memory_llm import LLMClient, LLMConfigError  # local import — optional dep
    except Exception as exc:  # pragma: no cover — defensive
        return None, error_result("llm_unavailable", f"memory_llm import failed: {exc}")
    try:
        return LLMClient(), None
    except LLMConfigError as exc:
        return None, error_result("llm_unavailable", str(exc))
    except Exception as exc:  # pragma: no cover — defensive
        return None, error_result("llm_unavailable", f"failed to build LLMClient: {exc}")
