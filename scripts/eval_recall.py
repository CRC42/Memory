"""§15.1-C: recall@K + MRR harness for the vector retrieval tier.

Loads a curated query → expected ``record_id`` set from
``tests/data/recall_set.jsonl`` and reports::

    recall@5  recall@10  MRR  provider_id  model_hash  n_queries

This stays intentionally tiny so it can run in CI without optional
dependencies (the deterministic-hash provider is a pure-Python lexical
baseline).  Pass ``--provider local-onnx --model-path ...`` to evaluate
the ONNX tier locally; CI keeps the deterministic baseline so the
recall floor cannot silently regress when the LLM/embedding stack is
swapped.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from servers.memory_server.memory_config import load_config  # noqa: E402
from servers.memory_server.memory_embeddings import get_provider  # noqa: E402
from servers.memory_server.memory_vector_search import (  # noqa: E402
    build_vector_index,
    vector_search,
)


@dataclass
class RecallCase:
    query: str
    expected: set[str]
    tag: str = ""


def _load_cases(path: Path) -> list[RecallCase]:
    cases: list[RecallCase] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        row = json.loads(raw)
        cases.append(
            RecallCase(
                query=row["query"],
                expected=set(row.get("expected_record_ids", [])),
                tag=row.get("tag", ""),
            )
        )
    if not cases:
        raise ValueError(f"no recall cases loaded from {path}")
    return cases


def _hits_to_record_ids(hits: Iterable[dict]) -> list[str]:
    """``vector_search`` returns chunk-level hits; deduplicate to record
    IDs preserving best-first order.
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for hit in hits:
        rid = str(hit.get("record_id", ""))
        if rid and rid not in seen_set:
            seen.append(rid)
            seen_set.add(rid)
    return seen


def _recall_at(ranked: list[str], expected: set[str], k: int) -> float:
    if not expected:
        return 0.0
    top = set(ranked[:k])
    return len(top & expected) / len(expected)


def _reciprocal_rank(ranked: list[str], expected: set[str]) -> float:
    for idx, rid in enumerate(ranked, start=1):
        if rid in expected:
            return 1.0 / idx
    return 0.0


def evaluate(
    config_path: Path,
    cases_path: Path,
    *,
    top_k: int = 10,
    rebuild_index: bool = True,
) -> dict:
    config = load_config(config_path)
    if rebuild_index:
        build_vector_index(config)
    cases = _load_cases(cases_path)

    provider = None  # let vector_search resolve from config
    metadata = None
    rows = []
    sum_r5 = 0.0
    sum_r10 = 0.0
    sum_mrr = 0.0
    for case in cases:
        result = vector_search(config, case.query, top_k=top_k, provider=provider)
        if metadata is None and result.get("ok"):
            # Probe metadata from the index by piggy-backing a tiny call.
            from servers.memory_server.memory_vector_search import _resolve_provider

            metadata = _resolve_provider(config).metadata
        ranked = _hits_to_record_ids(result.get("hits", []))
        r5 = _recall_at(ranked, case.expected, 5)
        r10 = _recall_at(ranked, case.expected, top_k)
        mrr = _reciprocal_rank(ranked, case.expected)
        sum_r5 += r5
        sum_r10 += r10
        sum_mrr += mrr
        rows.append(
            {
                "query": case.query,
                "tag": case.tag,
                "expected": sorted(case.expected),
                "top": ranked[:top_k],
                "recall@5": r5,
                "recall@10": r10,
                "rr": mrr,
            }
        )
    n = len(cases)
    return {
        "provider_id": metadata.provider_id if metadata else None,
        "model_hash": metadata.model_hash if metadata else None,
        "n_queries": n,
        "recall@5": sum_r5 / n,
        f"recall@{top_k}": sum_r10 / n,
        "mrr": sum_mrr / n,
        "rows": rows,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Memory MCP recall harness (§15.1-C)")
    p.add_argument("--config-path", type=Path, required=True,
                   help="repo root that contains .ai-memory/config.json")
    p.add_argument("--cases", type=Path,
                   default=REPO_ROOT / "tests" / "data" / "recall_set.jsonl",
                   help="JSONL file: {query, expected_record_ids, tag}")
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--no-rebuild", action="store_true",
                   help="skip build_vector_index (use existing on-disk index)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of a summary line")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = evaluate(
        args.config_path,
        args.cases,
        top_k=args.top_k,
        rebuild_index=not args.no_rebuild,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"provider={report['provider_id']} "
            f"model_hash={report['model_hash']} "
            f"n={report['n_queries']} "
            f"recall@5={report['recall@5']:.3f} "
            f"recall@{args.top_k}={report[f'recall@{args.top_k}']:.3f} "
            f"mrr={report['mrr']:.3f}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
