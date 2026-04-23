from __future__ import annotations

from pathlib import Path

from servers.memory_server.memory_compiler import memory_compare_snapshots, memory_compile
from servers.memory_server.memory_config import load_config
from servers.memory_server.memory_records import memory_write_record
from servers.memory_server.memory_retrieval import memory_retrieve_context
from servers.memory_server.server import _dispatch_tool


def test_p3_snapshot_targets_generate_chain_and_compare(repo: Path) -> None:
    config = load_config(repo)
    first = memory_write_record(
        config,
        content_markdown="# Earlier Decision\n\nTexture import still used the old path.\n",
        record_kind="decision",
        scope="project_shared",
        status="validated",
        author="alice",
        tags=["mcp"],
        occurred_at="2026-04-22T10:00:00+00:00",
        cognitive_level="fa",
        system_area="memory",
    )
    second = memory_write_record(
        config,
        content_markdown="# Later Decision\n\nTexture import uses the new path.\n",
        record_kind="decision",
        scope="project_shared",
        status="validated",
        author="alice",
        tags=["mcp"],
        occurred_at="2026-04-23T10:00:00+00:00",
        cognitive_level="fa",
        system_area="memory",
    )

    day1 = memory_compile(config, target="daily_snapshot", as_of="2026-04-22")
    day2 = memory_compile(config, target="daily_snapshot", as_of="2026-04-23")
    weekly = memory_compile(config, target="weekly_snapshot", as_of="2026-04-23")
    monthly = memory_compile(config, target="monthly_snapshot", as_of="2026-04-23")
    compared = memory_compare_snapshots(config, path=day1["path"], other_path=day2["path"])

    assert day1["ok"] is True
    assert day2["ok"] is True
    assert weekly["ok"] is True
    assert monthly["ok"] is True
    assert day1["path"] == "memory-bank/compiled/snapshots/daily/2026-04-22.md"
    assert first["id"] in day1["included_record_ids"]
    assert second["id"] not in day1["included_record_ids"]
    assert second["id"] in day2["included_record_ids"]
    assert day1["snapshot_id"] in weekly["derived_from_snapshot_ids"]
    assert day2["snapshot_id"] in weekly["derived_from_snapshot_ids"]
    assert weekly["snapshot_id"] in monthly["derived_from_snapshot_ids"]
    assert compared["ok"] is True
    assert [item["id"] for item in compared["added"]] == [second["id"]]
    assert [item["id"] for item in compared["removed"]] == [first["id"]]


def test_p3_scoring_review_digests_and_rollback_context(repo: Path) -> None:
    config = load_config(repo)
    dao = memory_write_record(
        config,
        content_markdown="# Stable Principle\n\nMemory views are rebuildable, sources are truth.\n",
        record_kind="system_rule",
        scope="project_shared",
        status="published",
        author="lead",
        tags=["mcp"],
        occurred_at="2026-04-23T09:00:00+00:00",
        cognitive_level="dao",
        memory_tier="hot",
    )
    fa = memory_write_record(
        config,
        content_markdown="# Governance Rule\n\nSnapshot outputs must not rewrite source records.\n",
        record_kind="decision",
        scope="project_shared",
        status="validated",
        author="lead",
        tags=["mcp"],
        occurred_at="2026-04-23T10:00:00+00:00",
        cognitive_level="fa",
        task_id="task_p3",
        module_names=["MemoryServer"],
        system_area="memory",
    )
    shu = memory_write_record(
        config,
        content_markdown="# Operator Procedure\n\n## Next Steps\n\n- Run snapshot compile.\n- Review the queue.\n",
        record_kind="procedure",
        scope="project_shared",
        status="validated",
        author="lead",
        tags=["mcp"],
        occurred_at="2026-04-23T11:00:00+00:00",
        cognitive_level="shu",
        task_id="task_p3",
        module_names=["MemoryServer"],
        supersedes=[fa["id"]],
        system_area="memory",
    )

    dao_digest = memory_compile(config, target="dao_digest")
    fa_digest = memory_compile(config, target="fa_digest")
    shu_digest = memory_compile(config, target="shu_digest")
    review = memory_compile(config, target="review_queue")
    rollback = memory_compile(config, target="rollback_context", task_id="task_p3")

    assert dao_digest["ok"] is True
    assert fa_digest["ok"] is True
    assert shu_digest["ok"] is True
    assert dao["id"] in dao_digest["included_record_ids"]
    assert fa["id"] in fa_digest["included_record_ids"]
    assert shu["id"] in shu_digest["included_record_ids"]
    assert review["ok"] is True
    assert review["ranked"][0]["importance_score"] >= review["ranked"][-1]["importance_score"]
    assert rollback["ok"] is True
    assert fa["id"] in rollback["included_record_ids"]
    assert shu["id"] in rollback["included_record_ids"]


def test_p3_retrieve_context_assembles_required_sections(repo: Path) -> None:
    config = load_config(repo)
    rule = memory_write_record(
        config,
        content_markdown="# Texture Pipeline Rule\n\n## Decision\n\nUse deterministic context assembly for texture pipeline work.\n",
        record_kind="decision",
        scope="project_shared",
        status="validated",
        author="alice",
        tags=["mcp"],
        occurred_at="2026-04-23T09:00:00+00:00",
        cognitive_level="fa",
        module_names=["MemoryServer"],
        system_area="memory",
    )
    evidence = memory_write_record(
        config,
        content_markdown="# Texture Pipeline Evidence\n\nThe new texture pipeline path was observed in dispatch tests.\n",
        record_kind="observation",
        scope="session",
        status="raw",
        author="alice",
        tags=["mcp"],
        occurred_at="2026-04-23T10:00:00+00:00",
        cognitive_level="shu",
        module_names=["MemoryServer"],
        system_area="memory",
    )
    other = memory_write_record(
        config,
        content_markdown="# Other Area\n\nShould be removed by facet filtering.\n",
        record_kind="decision",
        scope="project_shared",
        status="validated",
        author="alice",
        tags=["mcp"],
        occurred_at="2026-04-23T11:00:00+00:00",
        cognitive_level="fa",
        module_names=["OtherModule"],
        system_area="other",
        conflicts_with=[rule["id"]],
    )
    memory_compile(config, target="daily_snapshot", as_of="2026-04-23")

    direct = memory_retrieve_context(
        config,
        query="texture pipeline",
        window_start="2026-04-23T00:00:00+00:00",
        window_end="2026-04-23T23:59:59+00:00",
        system_area="memory",
        module_names=["MemoryServer"],
        top_k=5,
    )
    facade = _dispatch_tool(
        config,
        "memory_context",
        {
            "operation": "retrieve_context",
            "query": "texture pipeline",
            "system_area": "memory",
            "module_names": ["MemoryServer"],
            "top_k": 5,
        },
    )
    compared = _dispatch_tool(
        config,
        "memory_context",
        {
            "operation": "compare_snapshots",
            "path": "memory-bank/compiled/snapshots/daily/2026-04-23.md",
            "other_path": "memory-bank/compiled/snapshots/daily/2026-04-23.md",
        },
    )

    assert direct["ok"] is True
    assert facade["ok"] is True
    selected_ids = {item["id"] for item in direct["selected_records"]}
    assert rule["id"] in selected_ids
    assert evidence["id"] in selected_ids
    assert other["id"] not in selected_ids
    assert direct["core_constraints"]
    assert direct["relevant_rules"]
    assert direct["key_evidence"]
    assert direct["recent_snapshots"][0]["snapshot_id"] == "daily_snapshot:2026-04-23"
    assert direct["pipeline"]["scope_filter"] >= direct["pipeline"]["facet_filter"]
    assert compared["ok"] is True
    assert compared["stats"]["added"] == 0
