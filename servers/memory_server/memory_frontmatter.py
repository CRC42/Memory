"""Front Matter parser/dumper for memory records.

Extracted from `memory_records.py` (P1-C). Pure functions; no schema validation.
The small YAML subset supported here is intentional: scalars, quoted strings,
nulls, ints/floats, and one-level lists with `- ` prefixes. This keeps the
truth-source files reviewable in a plain editor without requiring a full YAML
runtime.
"""

from __future__ import annotations

import re
from typing import Any

_SCALAR_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _parse_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if value in {"null", "None", "~"}:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    if _SCALAR_RE.match(value):
        if "." in value:
            return float(value)
        return int(value)
    return value


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if (
        not text
        or _SCALAR_RE.match(text)
        or text in {"null", "None", "~", "true", "false"}
        or any(char in text for char in [":", "#", "[", "]", "{", "}", ","])
        or text != text.strip()
    ):
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text


def parse_front_matter(front_matter: str) -> dict[str, Any]:
    """Parse the small YAML subset used by memory records."""
    parsed: dict[str, Any] = {}
    current_list_key: str | None = None

    for raw_line in front_matter.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError("list item found before a key")
            parsed.setdefault(current_list_key, []).append(_parse_scalar(stripped[2:]))
            continue
        if ":" not in stripped:
            raise ValueError(f"invalid front matter line: {raw_line}")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError("front matter key must not be empty")
        if raw_value.strip() == "":
            parsed[key] = []
            current_list_key = key
        else:
            parsed[key] = _parse_scalar(raw_value)
            current_list_key = None

    return parsed


def parse_record_markdown(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("record markdown must start with front matter")
    try:
        front_matter, body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError("record markdown front matter is not closed") from exc
    return parse_front_matter(front_matter), body.lstrip("\n")


def dump_front_matter(metadata: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in metadata.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_format_scalar(item)}")
        else:
            lines.append(f"{key}: {_format_scalar(value)}")
    return "\n".join(lines)


def render_record_markdown(metadata: dict[str, Any], body: str) -> str:
    return f"---\n{dump_front_matter(metadata)}\n---\n\n{body.strip()}\n"


__all__ = [
    "parse_front_matter",
    "parse_record_markdown",
    "dump_front_matter",
    "render_record_markdown",
]
