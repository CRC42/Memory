# DEVLOG - MCP Memory

## 2026-04-24 (v0.5.3: P1 批次重构 — server/compiler/budget 拆分 + 写入加固 + evidence_refs 扩展)

> **状态：6 项 P1 全部落地；`server.py` 1348 → 102 行；新增 4 个独立模块；新增 26 项回归测试；总计 184 测试全部通过（146 → 184，+38）**

### 范围

按 P1 评估顺序执行：P1-C → P1-A → P1-B → P1-F/G → P1-E → P1-D。每项均补充针对性测试，高风险项（写入加固、budget 拆分）含独立回归覆盖。

### 落地清单

1. **P1-C：抽 `memory_frontmatter.py`**
   - 从 `memory_records.py` 提取 7 个 YAML Front Matter 处理函数（`parse_front_matter` / `dump_front_matter` / `parse_record_markdown` / `render_record_markdown` / `_parse_scalar` / `_format_scalar` / `_SCALAR_RE`）。
   - `memory_records.py` 通过 import re-export 维持原符号可见，行数下降。
   - 新增 `tests/memory_server/test_frontmatter_roundtrip.py`（8 测试）：标量/列表往返、CJK Unicode、引号特殊字符、null/bool 字面量、缺头/未闭合的错误路径、re-export 一致性。
2. **P1-A：拆 `server.py`（1348 → 102 行）**
   - 新增 `server_descriptions.py`（`SERVER_NAME` / `SERVER_VERSION` / `_BASE_DESCRIPTIONS`）、`server_tools.py`（`_build_file_roles` / `_build_facade_tools` / `_build_legacy_tools` / `_build_tools`）、`server_dispatch.py`（`_check_required` / `_dispatch_memory_read` / `_dispatch_memory_write` / `_dispatch_memory_context` / `_dispatch_tool`）。
   - `server.py` 改为 thin entry-point + back-compat re-export，11 个测试模块的旧 import 全部继续工作。
   - 新增 `tests/memory_server/test_server_split.py`（11 测试）：facade 默认 3 个工具、`expose_admin_tools=true` 注册 22 个、`memory_write` 不重复、context schema 中所有 operation 均被 dispatch 覆盖、unknown tool 返回 `unknown_tool` 错误等。
3. **P1-B：抽 `memory_compiler_cache.py`**
   - 提取 `load_compile_cache_entries` / `find_compile_cache_entry` / `record_usage_stats` / `get_record_last_used_at`（即 `.ai-memory/compile-cache/*.json` 与 `.ai-memory/usage-stats.json` 的纯文件 I/O 层）。
   - `memory_compiler.py` 通过 `_record_usage_stats = record_usage_stats` 别名保持原内部调用，外部 5 个测试 import 通过 re-export 不变。
4. **P1-F/G：写入安全加固（`memory_record_io.py`）**
   - 新增 `_atomic_write_text(target, content)`：tmp 文件用 `O_CREAT | O_EXCL | O_WRONLY` 创建（防止同 tick 内并发 tmp 冲撞）；`fh.flush()` + `os.fsync()`（best-effort）后再 `os.replace`；tmp 与 target 强制同目录避免跨卷。
   - `write_same_record` 与 `write_record_to_target` 全部改用此助手，去除原本散落的 try/except 临时路径逻辑。
   - `write_record_to_target` 在 `same_path == False` 时新增「目标已存在则拒绝覆盖」防护，返回 `target_exists` 错误并保留原 candidate 文件，杜绝悄默 clobber 已发布记录的可能。
   - 新增 `tests/memory_server/test_record_io_hardening.py`（6 测试）：成功路径、覆盖既有文件、自动建父目录、O_EXCL 拒绝（注入固定 uuid + 预占 tmp 文件触发 `OSError`，且不破坏他人文件）、`target_exists` 拒绝路径、`write_same_record` 往返。
5. **P1-E：`important_memories.evidence_refs` 扩展**
   - 之前只聚合 `source_refs`。现追加 `related_artifact_ids`、记录 `path`、记录 `id`，供消费方做完整 provenance 审计。
   - 同步在 `_build_memory_item` 输出加上 `related_artifact_ids` 字段。
   - 新增 `tests/memory_server/test_evidence_refs.py`：写入 candidate → validate → publish 后断言 evidence_refs 同时包含 `source_refs` 元素 / artifact_id / 文件路径 / 记录 ID。
6. **P1-D：抽 `memory_budget.py`（共享 budget 原语）**
   - 提取 `IMPORTANT_MEMORY_DEFAULT_MAX_*` 常量、`validate_budget_inputs`、`fit_text_to_budget`。`memory_retrieval.py` 通过 `from .memory_budget import ... as _validate_budget_inputs / _fit_text_to_budget` 维持本地下划线别名，所有现有调用零修改。
   - 注意：未变更 `memory_retrieve_context` 公共返回结构（`test_p3_retrieve_context_accepts_budget_controls` 仍要求其不暴露 `budget_report` / `important_memories` / `dropped_candidates`），保持向后兼容。
   - 新增 `tests/memory_server/test_budget_primitives.py`（11 测试）：None/0/-1 边界、空输入、字符截断、token 截断、常量合理性、向后兼容别名 `is` 检查。

### 验证

- `C:\Work\GIT\ToolTest\.venv\Scripts\python.exe -m pytest MCP\Memory\tests\memory_server -q` → **191 passed in 2.41s**（146 → 191，+45）。
- 其中新增 `tests/memory_server/test_mcp_protocol.py`（7 测试）：通过真实 `mcp.server.Server.request_handlers` 调度 `ListToolsRequest` / `CallToolRequest`，端到端覆盖 facade 默认 3 工具、admin 模式 23 工具、memory_read/write/context 调用、unknown_tool 错误信封、admin 模式下 legacy `memory_get` 可达。
- `SERVER_VERSION` 已升至 `"0.5.3"`。
- 文件与符号映射：

| 旧位置 | 新位置 | 备注 |
|---|---|---|
| `memory_records.py` (7 funcs) | `memory_frontmatter.py` | re-export 别名保留 |
| `server.py` 大块逻辑 | `server_descriptions.py` / `server_tools.py` / `server_dispatch.py` | `server.py` ≈ 102 行 thin shim |
| `memory_compiler.py` cache+usage | `memory_compiler_cache.py` | re-export 别名保留 |
| `memory_retrieval.py` budget 工具 | `memory_budget.py` | re-export 别名保留 |

### 影响范围

- 公共 facade（`memory_read` / `memory_write` / `memory_context`）API 与返回结构未变。
- `MCP\Memory\servers\memory_server\server.py.bak` 已临时备份后清理。
- 模块依赖更清晰：`memory_budget` 仅依赖 `memory_result + token_estimator`；`memory_compiler_cache` 仅依赖 `memory_config + memory_corpus`；`server_*` 三件套形成 facade/dispatch/descriptions 边界。
- `_record_usage_stats` 在 compiler 内仍以 `_` 私有别名存在；外部仅 `find_compile_cache_entry` / `load_compile_cache_entries` / `get_record_last_used_at` 是公共接口。

### 后续待办

- `memory_compiler.py` 仍约 950 行（render / targets / scoring）尚未细拆，可作为 v0.5.4 候选；本轮风险/收益不划算未动。
- `memory_retrieve_context` 是否要在新版本暴露 `budget_report` 需先与现有 `test_p3_retrieve_context_accepts_budget_controls` 契约协商，列入 v0.6 设计议题。
- `_atomic_write_text` 的 `os.fsync` 在某些 Windows 卷会抛 `OSError`，目前 best-effort swallow；后续若发现实际生产场景中需要严格持久性，可加 config 开关。

---

## 2026-04-24 (v0.5.2: user_private 作者隔离 + memory_corpus 解耦)

> **状态：1 处 P0 安全缺陷修复；P1-3 解耦重构完成；新增 1 个回归测试；总计 146 测试全部通过**

### 背景

按 `MemorySystemDesignDocument.md` §0.3 / §15.5 / §15.6 收敛 v0.5.1 之后的剩余 P0/P1 项：

- P0-2：`schema v2` 引入 `user_private` 作用域，但 `memory_compiler._matches_filter` 与 `memory_retrieval._collect_records` 仍只对 V1 `personal` 执行作者隔离，导致 `user_private` 记录会被其他用户在 `memory_retrieve_context` / `important_memories` 中读到。
- P1-3：`memory_retrieval` 反向依赖 `memory_compiler` 的 `CompilableRecord` / `_compact_body` / `_iter_records` 等私有符号，违反层次方向。

### 修复与重构

1. **`user_private` 作者隔离（P0-2）**
   - `memory_compiler._matches_filter`：`scope == "personal"` → `scope in {"personal", "user_private"}`。
   - `memory_retrieval._collect_records`：抽出 `private_scopes = {"personal", "user_private"}` 并复用同一过滤分支。
   - 两条路径同步修复，确保 retrieval / compiler / important_memories 三个出口都不会越权。
2. **`memory_corpus.py` 抽离（P1-3）**
   - 新增 `MCP/Memory/servers/memory_server/memory_corpus.py`：包含 `CompilableRecord` 数据类、`first_heading` / `body_without_title` / `markdown_sections` / `clip_text` / `compact_body` / `iter_compilable_records` 与 `COMPACT_SECTION_PRIORITY` / `COMPACT_BODY_CHAR_LIMIT` 常量。
   - `memory_compiler.py`：删除本地实现，改为从 `memory_corpus` 导入并保留 `_first_heading` / `_compact_body` 等下划线别名供原有内部调用方过渡使用；`_iter_records` 简化为 `return iter_compilable_records(config)`。
   - `memory_retrieval.py`：`from .memory_compiler import ...` 改为 `from .memory_corpus import CompilableRecord, compact_body as _compact_body, iter_compilable_records as _iter_records`，不再依赖 compiler 私有符号。
3. **回归测试**
   - `tests/memory_server/test_p3_completion.py` 新增 `test_p3_private_scopes_isolate_authors_in_retrieval`：alice 分别写入 `personal` 和 `user_private` 各一条，bob 通过 `memory_retrieve_context` / `memory_get_important_memories` 都无法读到，alice 自己仍可读到。
   - 注意：`tags` 必须使用受控词表（如 `mcp`）；`pipeline` 等会被 `invalid_input` 拒绝。

### 验证

- `C:\Work\GIT\ToolTest\.venv\Scripts\python.exe -m pytest MCP\Memory\tests\memory_server -q` → **146 passed in 2.29s**（145 → 146）。
- 注意：`MCP/Memory/.venv` 不带 `pytest`，应使用仓库根 `.venv` 或 `scripts/run_memory_all_tests.ps1`（脚本会自动回退）。

### 影响范围

- 公共 facade（`memory_read` / `memory_write` / `memory_context`）行为未变。
- `memory_compiler` 仍对外保留 `_first_heading` / `_compact_body` / `CompilableRecord` 名称，可向后兼容外部潜在调用。
- 后续 P0-1 / P0-2（拆 `memory_compiler.py` / `server.py`）保持原计划，本次未触及。

---

## 2026-04-23 (健壮性深度测试 + 两处加固)

> **状态：新增 10 个健壮性测试；2 处真实缺陷已修复；总计 143 测试全部通过**

### 背景

P0-3 完成后对系统做一轮深度健壮性评估，覆盖原 133 测试矩阵未触达的边界：原子写并发、崩溃残留 `.tmp`、损坏 Front Matter、SQLite 索引文件损坏、路径攻击变体（NUL 字节 / 绝对路径 / `..` 链 / Windows 盘符）、Unicode 往返、全局预算上限、`memory_write_record` 拒绝非法元数据。

### 发现并修复的真实缺陷

1. **路径含 NUL 字节会让 `pathlib` 抛 `ValueError`，越过安全层。**
   - 修复：`memory_paths.PathManager.resolve` 在最前面拒绝 `\x00`，统一抛 `PathSecurityError`，调用方得到 `path_not_allowed` 而不是 500-级异常。
2. **SQLite 索引文件损坏后 `memory_rebuild_index` 永久失败，无法自愈。**
   - 修复：新增 `_is_index_healthy`（基于 `PRAGMA integrity_check`）与 `_reset_corrupted_index`（含 GC + 重试，处理 Windows 文件锁），rebuild 前自动检测并清掉坏 db / WAL / SHM 副本。

### 新增测试（`tests/memory_server/test_robustness_deep.py`）

- 并发 overwrite 不产生半截/交错文件（成功者写入完整 payload，失败者得到 `write_failed`）。
- 残留 `.tmp` 兄弟文件不阻塞下一次写入。
- `iter_parsed_records` 跳过坏 YAML / 无 Front Matter 的 markdown，并在 stats 中可观测。
- `find_record_by_id` 在空 corpus 下返回 `not_found`。
- 损坏 `search.db` 后 rebuild 自愈、search 可命中。
- 6 种路径攻击（含 NUL）全部被 `path_not_allowed` 拒绝。
- CJK + emoji + RTL 字符 write→read 字节级保持。
- 超出全局预算（5000 chars）被预先拒绝。
- 非法 `record_kind` 被拒绝且不留下任何文件。

### 验证

`cd MCP/Memory; ..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q` -> **143 passed**。

---

## 2026-04-23 (P0-3 重构：抽取 record IO 公共层)

> **状态：已实现，测试 133 全部通过**

### 背景

随着 P3 完成，22 个模块里有 4 处独立实现的 `_iter_records` / `_find_record` / `_refresh_index_if_exists` / `_write_record_to_target`（散落在 `memory_governance.py`、`memory_lineage.py`、`memory_maintenance.py`、`memory_compiler.py`），签名不一致、容易漂移。本轮把这一层抽出公共模块，作为 P0 重构的第一步。

### 改动

- 新增 `servers/memory_server/memory_record_io.py`：
  - `ParsedRecord` dataclass。
  - `iter_record_files(config)`：返回 `memory-bank/` 下未编译的 markdown 文件。
  - `iter_parsed_records(config)`：解析记录并附带 scan stats。
  - `find_record_by_id(config, record_id)`：返回 4 元组或 `error_result`。
  - `refresh_index_if_exists(config, path)`：FTS 索引存在则增量刷新，best-effort。
  - `write_same_record(...)`：原子重写同路径记录（用于 lineage facet 追加）。
  - `write_record_to_target(...)`：临时文件 + `os.replace` + 旧路径清理，用于 governance 状态迁移。
- `memory_governance.py`：删除本地 `_iter_record_paths` / `_find_record` / `_write_record_to_target` / `_refresh_index_if_exists`，改为从 `memory_record_io` 导入；`_other_records` 改用 `iter_parsed_records`。
- `memory_lineage.py`：删除本地 `_iter_records` / `_find_record` / `_write_same_record` / `_refresh_index_if_exists`；保留薄壳 `_iter_records` 适配旧 4 元组用法。
- `memory_maintenance.py`：删除本地 `_iter_record_files` / `_find_record`，改为公共层导入。
- `memory_compiler.py`：`_iter_records` 改为在 `iter_parsed_records` 之上做 `CompilableRecord` 投影，保留对外签名 `(records, stats)`。
- `memory_retrieval.py`：仍通过 `memory_compiler` 的 `CompilableRecord` 接口工作，间接受益。

### 受益

- 4 处重复实现 → 1 处公共实现，行为差异从此固定（特别是 `os.replace` 原子写入和 PathSecurityError 错误码）。
- 单文件最大行数：`memory_compiler.py` 1188→1158，`memory_governance.py` 326→215，`memory_lineage.py` 385→306。
- MCP 对外接口零变更，默认仍只暴露 3 个 facade。
- 测试零修改通过：`memory_server` 全量 133 passed。

### 后续重构计划（已识别，未实施）

- P0-1 拆 `memory_compiler.py`（cache / render / targets / 入口分文件）。
- P0-2 拆 `server.py`（schema / dispatch / admin / main 分文件）。
- P1-4 把 `CompilableRecord` 与 `_compact_body` 上提到独立 corpus 模块，消除 `memory_retrieval` 对 compiler 私有函数的反向依赖。
- P1-5 把 `parse_front_matter` / `dump_front_matter` 抽到 `memory_frontmatter.py`，便于未来替换实现。

### 验证

```powershell
cd MCP/Memory; ..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 133 passed in 2.02s
```

## 2026-04-23 (P3 completion：snapshots / scoring / retrieve context)

> **状态：已实现，测试 133 全部通过**

### 背景

上一轮已完成 schema v2、evidence/lineage 和 conflict listing。本轮收尾 P3B/P3C/P3D 剩余项，在不增加默认 MCP tool 数量的前提下，把时间快照、deterministic scoring、review/rollback 分层视图、snapshot compare 和 context retrieval v1 接入 `memory_context` / `memory_compile`。

### 改动

- 新增 `memory_scoring.py`：
  - `score_governance()`、`score_usage()`、`score_impact()`、`score_novelty()`、`score_conflict()`、`score_decay()`。
  - scorer 输出 deterministic `importance_score` 和 effective memory tier，不回写源记录。
- 扩展 `memory_compile`：
  - 新增 `daily_snapshot`、`weekly_snapshot`、`monthly_snapshot`。
  - 新增 `review_queue`、`rollback_context`、`dao_digest`、`fa_digest`、`shu_digest`。
  - compile cache 记录 snapshot id、窗口、派生 snapshot ids 和 included record ids。
- 新增 `memory_compare_snapshots()`：
  - 基于 compile cache 对比 added / removed / persisted record ids。
- 新增 `memory_retrieval.py`：
  - `memory_retrieve_context()` 固定执行 scope filter -> time window filter -> facet filter -> metadata/FTS recall -> importance rerank -> context assembly。
  - 输出 `core_constraints`、`relevant_rules`、`recent_snapshots`、`key_evidence`、`open_conflicts`、`next_steps`。
- `memory_context` 新增：
  - `operation="compare_snapshots"`
  - `operation="retrieve_context"`
- 默认 MCP facade 仍保持 3 个工具。

### 验证

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 133 passed
```

## 2026-04-23 (P3D conflict listing：memory_context list_conflicts)

> **状态：已实现，测试 130 全部通过**

### 背景

P3D 计划把检索升级为上下文装配，其中 open conflicts 是后续 `memory_retrieve_context` 和 review queue 的关键输入。本轮不新增 MCP tool，保持默认 3 个 facade，只在 `memory_context` 中增加一个 operation。

### 改动

- 新增 `memory_list_conflicts(config, include_resolved=False)`：
  - 扫描 Markdown + Front Matter 真源记录。
  - 读取 `conflicts_with` 谱系字段。
  - 返回冲突双方 record summary、缺失目标、resolved 状态和统计信息。
  - 默认隐藏已 archived / degraded 的 resolved 冲突，可用 `include_resolved=true` 查看。
- `memory_context` 新增 `operation="list_conflicts"`。
- 不新增 MCP tool，默认对外仍只有 `memory_read`、`memory_write`、`memory_context`。
- 更新 README 与设计文档 P3D 状态。

### 验证

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 130 passed
```

## 2026-04-23 (MCP facade 收敛实现：默认 3 工具)

> **状态：已实现，测试 127 全部通过**

### 背景

按最新开发计划，优先把 MCP 对外工具列表从开发期的细粒度工具集收敛为少量 facade，降低 AI 客户端误选低频管理工具的概率。

### 改动

- 默认 MCP tool list 只暴露 3 个工具：
  - `memory_read`
  - `memory_write`
  - `memory_context`
- `memory_read` 支持：
  - `operation="get"`
  - `operation="search"`
  - `operation="search_records"`
  - `operation="runtime_digest"`
- `memory_write` 支持：
  - `operation="file"`，并作为默认操作兼容旧 `memory_write(path, content, ...)` 调用
  - `operation="record"`
  - `operation="observation"`
  - `operation="link_artifact"`
- `memory_context` 支持：
  - `operation="compile"`
  - `operation="runtime_digest"`
  - `operation="trace_lineage"`
- 新增配置：

```json
{
  "mcp": {
    "expose_admin_tools": true
  }
}
```

- 默认 `mcp.expose_admin_tools=false`，只暴露 3 个 facade。
- 开启后暴露 facade + legacy/admin 工具，便于开发调试或纯 MCP 客户端兼容。
- 旧细粒度工具的 `_dispatch_tool` 兼容入口仍保留，内部函数未删除。

### 验证

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 127 passed
```

## 2026-04-23 (开发计划调整：MCP facade 收敛列为最高优先级)

> **状态：文档计划更新，无代码变更**

### 背景

当前 Memory MCP 为了开发验证和测试覆盖，已经暴露到 18/21 个细粒度工具。这个形态适合开发期调试，但对 AI 客户端不够友好，也会把 guard、backup、migrate、governance、delete 等低频或高风险管理动作直接放到默认工具列表里。

### 决策

后续最高优先级改为先收敛 MCP 对外接口：

- 默认 MCP 只暴露 3 个 facade tools：
  - `memory_read`
  - `memory_write`
  - `memory_context`
- 现有细粒度能力继续作为内部 Python 函数保留。
- 管理动作迁移到 CLI / scripts / skill：
  - guard / backup / compact
  - index rebuild/update
  - health / migrate
  - validate / publish / archive / delete
  - snapshot rebuild / compare
  - conflict review / promote / degrade
- 后续补管理 skill：
  - `memory-admin`
  - `memory-governance`
  - `memory-snapshot-review`
- 增加配置开关用于兼容开发期和纯 MCP 客户端，例如 `expose_admin_tools=true`。

### 文档

- 已更新 `MemorySystemDesignDocument.md` 的 MCP 接口设计、落地顺序建议和最终优先级。
- 已更新 `README.md` 的后续开发计划。

## 2026-04-23 (v0.5.0 P3A 继续推进：observation / artifact / lineage 工具)

> **状态：已实现，测试 124 全部通过**

### 背景

上一轮已完成 schema v2 字段、扩展 kind/scope、tier/cognitive/facet 索引基础。本轮继续沿 P3A/P3D 交界处推进，让 schema v2 不只是一组可写字段，而是具备基础证据写入、artifact 关联和谱系追踪入口。

### 改动

- 新增 `memory_lineage.py`。
- 新增 `memory_record_observation`：
  - 固定写入 `schema_version="2.0"`、`record_kind="observation"`、`scope="session"`、`status="raw"`。
  - 默认 `memory_tier="hot"`、`cognitive_level="shu"`。
  - 支持 artifact / 工程 facet 字段。
- 新增 `memory_link_artifact`：
  - 给既有记录追加 `related_artifact_ids`、`asset_paths`、`map_names`、`plugin_names`、`module_names`、`class_names`、`blueprint_paths`、`system_area`。
  - 自动升级记录为 schema v2。
  - 如果 `.ai-memory/search.db` 已存在，会增量刷新对应记录索引。
- 新增 `memory_trace_lineage`：
  - 从指定记录开始追踪 `derived_from_record_ids`、`supersedes`、`conflicts_with`。
  - 返回 `nodes`、`edges`、`missing` 和统计信息。
- MCP 工具数从 18 增至 21。
- README 与设计文档已同步 P3A 当前状态。

### 验证

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 124 passed
```

## 2026-04-23 (v0.5.0 P3A 启动：schema v2 记录模型基础)

> **状态：已实现，测试 119 全部通过**

### 背景

按新的 P3 结构升级计划，优先从不依赖 LLM 的记录模型扩展开始。目标是让现有 Markdown + Front Matter 真源先能承载时间、分层、谱系和工程 facet 信息，并让 SQLite 派生索引可以检索这些结构化字段。

### 改动

- `memory_write_record` 支持 `schema_version="2.0"`，并在使用 P3 record kind、scope 或 v2 字段时自动升级为 schema v2。
- 扩展 `record_kind`：
  - `observation`
  - `artifact_ref`
  - `incident`
  - `decision`
  - `procedure`
  - `snapshot_daily`
  - `snapshot_weekly`
  - `snapshot_monthly`
- 扩展 `scope`：
  - `session`
  - `user_private`
  - `task_or_branch`
  - `project_shared`
  - `org_shared`
- 新增 v2 metadata 字段：
  - 时间：`occurred_at`、`valid_from`、`valid_to`
  - 分层：`memory_tier`、`cognitive_level`
  - 谱系：`derived_from_record_ids`、`derived_from_snapshot_ids`、`derived_from_revision_ids`、`supersedes`、`conflicts_with`
  - 工程 facet：`related_artifact_ids`、`asset_paths`、`map_names`、`plugin_names`、`module_names`、`class_names`、`blueprint_paths`、`system_area`
  - 评分预留：`importance_score`
- `memory_record_index.py` 将 schema v2 字段写入 SQLite metadata 表，并把 tier、cognitive level、system area 和 facets 纳入 FTS 搜索文本。
- MCP `memory_write_record` 工具 schema 同步暴露 P3A 新字段。

### 兼容性

- 默认写入仍保持 schema `1.0`，旧记录和旧调用方不受影响。
- 如果显式传 `schema_version="1.0"` 同时使用 P3 kind / scope / v2 字段，会返回 `invalid_input`，避免 silently 丢字段。
- `memory-bank/shared/` 现在承接 `project_shared` / `org_shared` 记录；个人、任务和会话级记录仍进入 `memory-bank/people/{user}/`。

### 验证

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 119 passed
```

## 2026-04-23 (开发计划重排：P3 结构升级优先)

> **状态：文档规划更新，无代码变更**

### 背景

此前 README / 设计文档将后续方向写成 P3 LLM 增强、P4 本地 RAG / 向量召回。结合后续评估，当前系统真正更急的不是先接 LLM，而是把现有 Markdown + Front Matter 真源、SQLite 派生索引、deterministic compile、governance 主干升级为可支撑多人联合项目的结构化记忆编译器。

### 调整

- 新 P3：结构升级与联合项目记忆能力。
  - schema v2
  - daily / weekly / monthly snapshot
  - lineage / derived_from / supersedes / conflicts_with
  - memory_tier: hot / warm / cold / fossil
  - cognitive_level: dao / fa / shu
  - importance scoring
  - facet / artifact / scope 扩展
  - `memory_retrieve_context` 上下文装配
- 新 P4：LLM 软增强。
  - query rewrite
  - tag / facet 推荐
  - candidate draft
  - snapshot narrative
  - conflict explanation
- 新 P5：本地 RAG / 向量补召回。
  - 仅做语义模糊召回、长尾别名补召回、低关键词命中补召回。

### 约束

- 真源仍然是 Markdown + Front Matter。
- SQLite / FTS / CJK n-gram 仍然只是派生索引。
- deterministic compile 仍然是主编译链路。
- governance 仍然是正式发布入口。
- 无 LLM 必须完整可运行。
- LLM 和向量检索均不得替代真源、发布权限或 deterministic compile。

### 维护

- 已运行 `memory_compact(policy=warm_context)` 压缩 `memory-bank/activeContext.md`：约 8974 chars -> 2576 chars。

## 2026-04-22 (v0.4.2 编译默认 compact 输出)

> **状态：已实现，测试 115 全部通过**

### 背景

实测多段记忆编译后发现，旧 `runtime_digest` 能按 task/status/tag 过滤记录，但默认会把匹配记录的详细 metadata 和完整正文都写入编译产物。它能减少“不相关记录”的上下文，却不能减少“相关记录本身”的上下文体积。

### 改动

- `memory_compile` 新增 `body_mode` 参数。
- 默认 `body_mode="compact"`：
  - 每条记录只输出 `id`、`source`、`status`。
  - 不逐条输出 `author`、`task_id`、`branch`、`tags` 等筛选 metadata。
  - 优先抽取 `Decision`、`Expected Behavior`、`Acceptance Checks`、`Next Step(s)`、`Notes`、`Details` 等关键段落。
  - 没有关键段落时截取正文开头。
- 保留 `body_mode="full"`，用于旧版完整记录渲染和调试。
- MCP `memory_compile` schema 暴露 `body_mode`，并在 cache manifest / audit event / tool result 中记录实际模式。
- 修复 `Resolve-MemoryTestPython.ps1`：候选 Python 缺少 pytest 时继续尝试下一个环境，避免官方测试脚本在回退到仓库 `.venv` 前中断。

### 验证

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 115 passed
```

## 2026-04-21 (后续开发计划：LLM 与 RAG 优先级)

> **状态：历史规划；已被 2026-04-23 的 P3 结构升级路线后移和替代**

### 结论

- P3 仍以 LLM 增强为优先方向：分类、tag 推荐、候选生成、冲突解释和摘要提炼。
- 不再把中文分词增强列为独立计划；当前继续保留已实现的 CJK bigram/trigram 作为中文检索兜底。
- 中文模糊查询优先通过 LLM query rewrite / metadata hint 增强，再交给当前 FTS 检索。
- 本地 RAG / 向量召回排在 LLM 增强之后，仅用于语义模糊召回、相似记录推荐和候选冲突提示。
- RAG 索引必须是 `.ai-memory/` 下的派生产物，可删除重建，不能替代 Markdown + Front Matter 真源。

### 约束

- LLM 和 RAG 都不能直接发布系统记忆。
- `candidate -> validated -> published` 治理流不变。
- 无 LLM、无向量模型时，基础写入、FTS 检索、编译和治理必须继续可用。

## 2026-04-21 (v0.4.1 严肃评审 P0 修复轮)

> **状态：已实现，向后兼容；测试 106 → 113 全部通过**

### 背景

针对 vNext 设计目标做了一轮严肃评审，定位到 7 个会在多人 / 多客户端 / 真实文本场景下踩雷的正确性与设计风险，本次集中修复。

### 修复清单

1. **FTS5 MATCH 查询语法**（`memory_record_index.py`）
   - 旧实现 `f"search_text : {query_text}"` 同时存在两个错误：
     1) FTS5 列限定符不允许空格；
     2) 用户输入直接拼接，含 `-` / `OR` / `"` / `:` 时会触发 `OperationalError`。
   - 新实现 `build_fts5_match_query()`：复用索引侧 tokenizer，对每个 token 加双引号包成 phrase，去掉列限定符（`search_text` 仍是覆盖最广的索引列，所有列 MATCH 等价或更宽）。
   - 新增 `_escape_fts5_token` / `build_fts5_match_query` 公共函数，便于复用与单测。

2. **编译器禁止反向修改源记录**（`memory_compiler.py`）
   - 旧 `_mark_records_used` 会把 `last_used_at` 写回真源 `.md`，违反"编译产物可重建、源是真源"的设计原则，污染 Git diff，且不刷新 FTS 索引。
   - 新实现 `_record_usage_stats` 把使用情况写到 `.ai-memory/usage-stats.json`（与 compile-cache 同级，可整体删除重建）。
   - 新增 `get_record_last_used_at(config, record_id)` 公共读 API。

3. **记录写入原子化**（`memory_records.py`）
   - 改用 `os.open(path, O_CREAT | O_EXCL | O_WRONLY)` 创建记录，关闭 `already_exists` 检查与 `write_text` 之间的 TOCTOU 窗口；并发 MCP 客户端碰到同 id 时一方失败而非互相覆盖。
   - 写入失败时回滚被 `O_EXCL` 创建出来的空文件，避免留下半成品。

4. **治理状态迁移原子化**（`memory_governance.py`）
   - 旧 `write_text(new) → unlink(old)` 路径在异常时可能留下两份或丢失记录。
   - 新实现：临时文件 `tmp` → `os.replace(tmp, new)` → 仅在新路径就绪后删除旧路径；任一步失败都不会破坏既有真源。

5. **`memory_write` 用户标签注入策略**（`memory_writer.py` + `server.py`）
   - 旧实现无差别向所有写入末尾注入 `<!-- last overwritten by ... -->`；写入 JSON / YAML / TOML / 源码会破坏文件。
   - 新实现新增 `inject_user_tag` 参数：默认按扩展名自动判断（仅 `.md` / `.markdown` 注入），`True` 强制开启，`False` 强制关闭。
   - MCP `memory_write` 工具 schema 同步暴露 `inject_user_tag`。

6. **`resolve_user_path` 自动迁移加归属警告**（`memory_paths.py`）
   - 旧逻辑会把多人共写的旧 `activeContext.md` 直接复制到第一个登场的用户分区文件，造成事实归属错误。
   - 新逻辑在迁移内容前插入 `<!-- migrated-from-shared: ... attribution to '{user}' is NOT verified ... -->` banner，提示人工核对。

7. **审计日志 rotation**（`memory_events.py`）
   - 新增 `_rotate_events_if_needed()`：`events.jsonl` 超过阈值（默认 5 MB，可由 `MEMORY_MCP_EVENTS_MAX_BYTES` 覆盖）时按时间戳重命名为 `events.jsonl.YYYYMMDDTHHMMSS`，仅保留最新 N 份归档（默认 5，可由 `MEMORY_MCP_EVENTS_MAX_ARCHIVES` 覆盖）。
   - rotation 过程中的所有 OS 错误都被吞掉，绝不阻塞审计写入路径。

8. **去除默认 tag 词表的双源**（`memory_config.py` + `memory_records.py`）
   - 把内置默认 tag 集合提取为 `memory_config.DEFAULT_ALLOWED_TAGS` 单一定义。
   - `memory_records.ALLOWED_TAGS` 改为从 `memory_config` 引入；`DEFAULT_CONFIG_CONTENT` 也复用同一份列表。后续增减 tag 只改一处。

### 新增测试

- `test_record_index.py::test_search_records_handles_fts5_reserved_characters` — 覆盖 `texture-size` / `round-trip` / `OR fallback` / `"quoted phrase"` 四种触发旧 syntax error 的查询。
- `test_record_index.py::test_search_records_empty_after_normalization_returns_no_hits` — 全标点查询不再抛错。
- `test_record_index.py::test_search_records_query_plan_uses_sqlite_fts_index` — 同步到新查询表达式。
- `test_runtime_maintenance.py::test_compile_updates_last_used_at_and_writes_cache_manifest` — 改为断言"源记录未被改动 + `usage-stats.json` 写入 + 二次编译不改源 mtime"。
- `test_governance.py::test_validate_candidate_does_not_leave_two_copies` — 状态迁移后无残留 staging 文件。
- `test_write.py::test_inject_user_tag_default_skips_non_markdown` — `.json` 写入不被注释污染，且仍是有效 JSON。
- `test_write.py::test_inject_user_tag_default_marks_markdown` — `.md` 默认仍带尾注。
- `test_write.py::test_inject_user_tag_can_be_force_disabled` — 显式关闭注入。
- `test_multi_user.py::test_user_scoped_migration_includes_attribution_banner` — 自动迁移携带归属警告 banner。

### 验证结果

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 113 passed

.\scripts\run_memory_all_tests.ps1
# 113 passed
```

### 行为兼容性说明

- MCP 工具数量不变（仍 18 个）。
- 默认场景下：旧的 `.md` 写入仍带用户尾注；旧的搜索查询仍可命中；治理迁移路径不变；候选发布规则不变。
- 行为差异需调用方注意：
  - `memory_compile` 不再修改源记录的 `last_used_at`；改读 `.ai-memory/usage-stats.json` 或 `get_record_last_used_at()`。
  - `memory_write` 写入非 `.md` 路径默认不再注入 HTML 注释；如需保留旧行为，显式传 `inject_user_tag=true`。
  - 多人模式下首次自动迁移会带 banner，下游脚本读取 `activeContext/{user}.md` 时若期望"纯净文本"需自行剥离 banner 行。

---

## 2026-04-21 (P0/P1/P2 完成: 治理质量、运行时维护、配置化)

> **状态：已实现，不包含 P3 LLM 增强**

### 背景

在记录写入、检索、编译和基础治理闭环之后，继续补齐非 LLM 的 P0/P1/P2 能力：治理质量、运行时使用追踪、维护健康检查、schema 迁移、增量索引和删除策略。

### 本次实现

#### P0 治理质量

- `governance.min_confidence`：候选验证时检查最低可信度。
- `governance.require_source_refs_for`：指定候选类型必须有 `source_refs`。
- `governance.reviewers`：限制 `validated_by`。
- `governance.publish_owners`：限制 `published_by`。
- 验证时检查重复标题/正文。
- 发布时检查与 existing shared published system rule 标题相同但正文不同的冲突。

#### P1 编译与运行时

- `memory_compile` 会更新 included records 的 `last_used_at`。
- `memory_compile` 会写入 `.ai-memory/compile-cache/{target...}.json` manifest。
- `runtime_digest` 会包含旧文件摘要 section：`activeContext.md` / `progress.md`，保持与旧文件级记忆兼容。

#### P2 配置化与维护

- `tag_schema.allowed_tags` / `tag_schema.version` 配置化。
- `memory_health_check`：检查缺失 metadata、未知 tag、缺失 `search.db`。
- `memory_migrate_records`：迁移记录 `schema_version`，写入 `schema_migrated_from`。
- `memory_update_index`：对指定记录路径增量更新 SQLite FTS。
- `memory_delete_record`：只允许删除 archived 记录，并写入 `.ai-memory/tombstones.jsonl`。
- MCP 工具列表从 14 个扩展为 18 个。
- 更新 `README.md` 与 `MemorySystemDesignDocument.md`。

#### 健壮性收口

- `memory_compile` 明确拒绝非 `list[str]` 的 `include_scopes` / `include_statuses` / `preferred_tags`，避免字符串被误拆成字符过滤器。
- `memory_get_runtime_digest` 在读文件前校验 `max_chars >= 0`，错误输入稳定返回 `invalid_input`。
- `memory_write_record` 的 tag schema 校验改为显式参数传递，去掉函数属性形式的隐式全局状态，避免多配置/并发场景串配置。
- `memory_compile` 的 legacy section 与 `last_used_at` 更新改为显式传入 config，去掉编译流程中的模块级临时状态。
- `memory_health_check` 增加未闭合 Front Matter 报告。
- `memory_update_index` 增加 `paths` 参数类型防御；治理记录移动后会在已有 `search.db` 上刷新索引。
- 新增 MCP stdio `tools/list` 烟测，确认 18 个工具可被 MCP 客户端发现。
- 修复 `scripts/run_memory_*_tests.ps1`：测试脚本优先使用 `MCP/Memory/.venv`，若该环境缺少 `pytest` 则自动回退到仓库根 `.venv`；同时从 `MCP/Memory` 目录执行 pytest，确保 `tests/memory_server/conftest.py` 被加载。

### TDD 验证

新增：

- `tests/memory_server/test_governance_quality.py`
- `tests/memory_server/test_runtime_maintenance.py`
- `tests/memory_server/test_robustness_edges.py`

覆盖：

- 缺少 `source_refs` 的候选验证拒绝。
- 低 `confidence` 的候选验证拒绝。
- 重复 candidate 拒绝。
- 与已发布系统规则冲突的 candidate 发布拒绝。
- 非 owner 发布拒绝。
- tag schema version 来自配置。
- 编译更新 `last_used_at`。
- 编译写入 compile-cache manifest。
- runtime digest 包含旧文件 section。
- health check 报告坏记录和缺失 search.db。
- schema migration 更新旧记录。
- `memory_update_index` 增量索引单条记录。
- 非 archived 记录禁止删除。
- archived 记录删除时写 tombstone。
- MCP dispatch 暴露维护工具。
- 缺失记录治理返回 `not_found`。
- 非列表编译过滤器返回 `invalid_input`。
- runtime digest 负数截断长度返回 `invalid_input`。
- dispatch 层错误参数不会触发内部异常。
- 配置化 tag schema 不会泄漏到其他 config。

验证结果：

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 106 passed

.\scripts\run_memory_all_tests.ps1
# 106 passed
```

---

## 2026-04-21 (vNext 治理闭环: validate / publish / archive)

> **状态：已实现**

### 背景

记录级写入、检索、编译已经可用，但候选记录仍缺少从 candidate 到 published / archive 的治理闭环。根据设计文档，系统记忆不能由 LLM 直接发布，必须经过验证与发布流程。

### 本次实现

- 新增 `memory_governance.py`。
- 新增 `memory_validate_candidate`：
  - 支持 candidate/raw -> validated。
  - 写入 `validated_by` 和 `updated_at`。
  - 将候选从 `memory-bank/candidates/` 移入对应治理层。
- 新增 `memory_publish_candidate`：
  - 只允许发布 `status=validated` 且带 `validated_by` 的记录。
  - 发布后写入 `published_by` / `published_at`。
  - 发布后进入 `memory-bank/shared/{id}.md`。
  - `*_candidate` 发布后转为 `system_rule`。
- 新增 `memory_archive_record`：
  - 将任意记录移入 `memory-bank/archive/{id}.md`。
  - 写入 `archive_reason` / `archived_at`。
- 扩展 `memory_compile`：
  - 新增 `target="system_digest"`。
  - 新增 `target="publish_queue"`。
- MCP 工具列表从 11 个扩展为 14 个。
- 更新 `README.md` 与 `MemorySystemDesignDocument.md`。

### TDD 验证

新增 `tests/memory_server/test_governance.py`，覆盖：

- candidate 验证后元数据更新，并移出候选池。
- validated candidate 发布为 shared system rule。
- 未验证 candidate 发布被拒绝。
- 任意记录可归档，并写入归档元数据。
- `publish_queue` 编译列出 candidate 记录。
- `system_digest` 编译列出 published shared rule。
- MCP dispatch 暴露并调用 validate / publish / archive。
- `_build_tools` 包含治理工具。

验证结果：

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 81 passed
```

---

## 2026-04-21 (vNext 编译层: runtime digest 与 task handoff)

> **状态：已实现**

### 背景

记录写入、Front Matter 解析、SQLite FTS 和记录搜索已经完成，但设计文档中的编译层仍缺失。根据 vNext 设计，编译必须是无 LLM 可运行、确定性、可重建的派生视图，而不是新的真源。

### 本次实现

- 新增 `memory_compiler.py`。
- 新增 `memory_compile`：
  - 支持 `target="runtime_digest"`。
  - 支持 `target="task_handoff"`。
  - 默认只包含 `validated` / `published` 记录。
  - 支持 `user`、`task_id`、`branch`、`include_scopes`、`include_statuses`、`preferred_tags` 过滤。
  - 个人记录在指定 `user` 时只包含该用户自己的记录。
  - 输出确定性 Markdown，不包含当前时间戳，重复编译内容稳定。
- 新增 `memory_get_runtime_digest`，读取已有 runtime digest。
- 编译产物路径：
  - `memory-bank/compiled/runtime/task/{task_id}.md`
  - `memory-bank/compiled/runtime/branch/{branch}.md`
  - `memory-bank/compiled/runtime/people/{user}-digest.md`
  - `memory-bank/compiled/runtime/system-digest.md`
  - `memory-bank/compiled/runtime/task/{task_id}-handoff.md`
- MCP 工具列表从 9 个扩展为 11 个。
- 更新 `README.md` 与 `MemorySystemDesignDocument.md`。

### TDD 验证

新增 `tests/memory_server/test_compile.py`，覆盖：

- `runtime_digest` 默认过滤 candidate，只包含 validated/published。
- personal 记录按 `user` 过滤。
- shared published 记录可被编译进 digest。
- 编译结果重复生成内容稳定。
- `memory_get_runtime_digest` 可读取已有编译产物。
- `task_handoff` 可生成交接视图。
- 未知 target 返回 `invalid_input`。
- MCP dispatch 暴露并调用 `memory_compile` / `memory_get_runtime_digest`。
- `_build_tools` 包含编译工具。

验证结果：

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 73 passed
```

---

## 2026-04-21 (测试加固: 多人读写与用户分区搜索)

> **状态：已完成，未改生产代码**

### 背景

Memory MCP 是项目协作基础设施，多人模式下的读写行为必须被测试明确固定，尤其是 `activeContext.md` 到 `activeContext/{user}.md` 的重定向、旧文件迁移、用户身份识别和共享文件 append 策略。

### 本次新增测试

新增 `tests/memory_server/test_multi_user.py`，覆盖：

- 同一逻辑路径 `memory-bank/activeContext.md` 在不同用户下写入独立文件：
  - `memory-bank/activeContext/alice.md`
  - `memory-bank/activeContext/bob.md`
- 不同用户读取同一逻辑路径时，只返回自己的用户分区文件。
- 首次读取旧版 `activeContext.md` 时自动迁移到当前用户分区，且保留旧单文件。
- 缺少有效用户身份时，`user_scoped` 写入返回 `user_required`。
- `.vscode/settings.json` 中的 `memory-mcp.userName` 优先于环境变量。
- 共享文件 `progress.md` 在 `append_only` 策略下将 overwrite 请求降级为 append。
- `memory_guard_check` 对每个用户分区文件分别报告。
- `_dispatch_tool` 下 `memory_write` / `memory_get` 使用同一套用户重定向。
- `memory_search` 可以扫描 `activeContext/{user}.md`，并可用 `include_paths` 定位单个用户文件。

验证结果：

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 65 passed
```

---

## 2026-04-21 (测试加固: SQLite FTS 查询计划)

> **状态：已完成，未改生产代码**

### 背景

记录级搜索已经通过功能测试证明能重建 `.ai-memory/search.db` 并命中记录，但还需要直接固定“搜索确实走 SQLite FTS 虚拟表索引”，避免后续改动退化成普通表扫描或绕过 FTS。

### 本次新增测试

- 在 `tests/memory_server/test_record_index.py` 新增 `test_search_records_query_plan_uses_sqlite_fts_index`。
- 测试会写入记录、重建索引、检查 `memory_records_fts` 包含 `search_text` 列。
- 使用 `EXPLAIN QUERY PLAN` 验证 `MATCH` 查询计划包含 `VIRTUAL TABLE INDEX`。

验证结果：

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 66 passed
```

---

## 2026-04-21 (记录级检索增强: 零依赖中文 n-gram)

> **状态：已实现**

### 背景

`memory_search_records` 初版依赖 SQLite FTS 默认 tokenizer。该方案对英文和 metadata 检索可用，但中文短词搜索效果不稳定；如果立即引入 `jieba` 等分词库，又会增加离线 wheel 预下载和部署维护成本。

### 本次实现

- 未新增任何 Python 第三方依赖。
- 在 `memory_record_index.py` 中新增 `build_search_text`：
  - 英文/数字/下划线/短横线按普通 token 保留。
  - 中文连续文本生成 bigram/trigram。
  - `tags`、`record_kind`、`scope`、`status`、`author`、`task_id`、`branch` 一并写入搜索文本。
- `memory_rebuild_index` 的 FTS 表新增 `search_text` 列。
- `memory_search_records` 查询统一转换为同一套 search text，再查 `search_text`。
- 增加旧版 `.ai-memory/search.db` 自动迁移：如果已有 FTS 表缺少 `search_text`，重建索引时自动 drop/recreate 派生表。
- 更新 `README.md` 与 `MemorySystemDesignDocument.md`，明确中文检索策略为无依赖 CJK n-gram。

### TDD 验证

- 新增中文短词搜索测试：`尺寸约束` 可命中 `导出链路尺寸约束`。
- 新增 metadata 搜索测试：`task_id` 可命中记录。
- 新增 tokenizer 单元测试：确认生成 CJK bigram/trigram。
- 新增旧 FTS schema 迁移测试。

验证结果：

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 55 passed
```

---

## 2026-04-21 (vNext 实现起步: 记录级写入与 SQLite FTS 索引)

> **状态：已实现首个 TDD 增量**

### 背景

基于 `MemorySystemDesignDocument.md` 的阶段 1 建议，本次没有推翻现有文件级接口，而是在 `v0.4.0` 的 `memory_get` / `memory_write` / `memory_search` / `memory_guard_check` 等能力旁边新增记录级基础能力。

### 本次实现

- 新增 `memory_write_record`，支持将结构化记忆写为 `Markdown + YAML Front Matter`。
- 新增记录级基础字段校验：`record_kind`、`scope`、`status`、受控 `tags`、`confidence` 范围。
- 新增候选、共享、个人、归档的基础落盘路由：
  - `memory-bank/candidates/{id}.md`
  - `memory-bank/shared/{id}.md`
  - `memory-bank/people/{user}/{id}.md`
  - `memory-bank/archive/{id}.md`
- 新增 Front Matter 解析与序列化工具，当前使用无额外依赖的 YAML 子集解析，保证无 LLM / 无新增运行时依赖时可用。
- 新增 `memory_rebuild_index`，从记录 Markdown 重建 `.ai-memory/search.db`。
- 新增 `memory_search_records`，通过 SQLite FTS 查询结构化记录，并返回记录元数据。
- MCP 工具列表从 6 个扩展为 9 个，同时保留既有文件级工具行为不变。

### TDD 验证

- 新增 `tests/memory_server/test_records.py` 覆盖记录写入、Front Matter 解析、非法枚举、受控标签和 dispatch。
- 新增 `tests/memory_server/test_record_index.py` 覆盖索引重建、记录级搜索和 dispatch。
- 更新动态工具描述测试，确认新增工具暴露在 MCP tool list 中。

验证结果：

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\memory_server -q
# 51 passed
```

### 后续建议

- 下一步可在现有记录层上继续实现 `memory_compile` / `memory_get_runtime_digest`。
- 候选验证、发布、归档流程仍应放在记录层稳定之后推进。
- 当前 Front Matter 解析器只覆盖本项目记录 schema 需要的 YAML 子集，若后续需要复杂 YAML，应再评估是否引入运行时依赖。

---

## 2026-04-21 (vNext 设计稿: 从文件级 Memory MCP 走向记录级治理与编译架构)

> **状态：设计方案，未执行开发**

### 背景

`v0.4.0` 已经解决了多人协作下最现实的几个问题：

- `activeContext` 高频写入冲突
- 共享记忆文件的 append 策略
- 用户分区重定向
- guard / backup / compaction / 审计等基础设施

但当前系统的主抽象仍然是“文件”，而不是“记忆记录”。这会带来几个后续开发阶段绕不开的问题：

1. 系统记忆、个人记忆、候选记录、归档记录还没有统一的结构化外壳
2. 检索仍以文件粒度为主，尚未形成记录级索引与检索排序能力
3. 缺少候选验证、正式发布、降级归档这条治理链路
4. 缺少运行时 digest、handoff、publish queue 等“编译产物”层
5. MCP 对外能力仍偏向文件操作，尚未升级为记录级接口

因此，本次设计稿的目标不是推翻 `v0.4.0`，而是在兼容现有 `memory_get` / `memory_write` / `memory_search` / `memory_guard_check` 工作方式的前提下，补齐 Memory MCP 的长期架构。

### 本次设计结论

#### 1. 真源定义上移到“记录层”

系统真源不再仅仅理解为几份 Markdown 摘要文件，而是以下对象的组合：

- Markdown 记忆文件
- YAML Front Matter 元数据
- 事件日志
- 候选记录
- 已发布系统记忆

LLM 只能参与提炼、分类和填表，不能成为唯一真源。

#### 2. 记忆分层从 3 层扩展为 5 层

在当前 `memory-bank/`、`.ai-context/`、`.ai-memory/` 的基础上，设计稿将记忆职责明确拆分为：

- 个人记忆
- 系统记忆
- 历史记忆
- 本地临时记忆
- 编译记忆

这意味着后续实现不应再把所有长期知识都塞进少数几个共享 Markdown 文件，而应引入 `shared/`、`people/{user}/`、`candidates/`、`archive/`、`compiled/` 等正式目录层。

#### 3. 正式存储格式确定为 Markdown + Front Matter

本次设计明确了后续正式记录格式：

- MCP 传输层使用 JSON
- 落盘正文使用 Markdown
- 结构化元数据使用 YAML Front Matter
- 检索索引使用 SQLite FTS
- 审计与事件流使用 JSONL

这一步很关键，因为它把当前“文件写入工具”继续保留下来，同时为后续的记录级 schema、索引、编译和发布能力提供稳定底座。

#### 4. 正式引入“记忆编译”概念

设计稿将编译定义为一种确定性聚合过程，而不是让 LLM 再写一遍总结。编译输入优先依赖结构化字段，例如：

- `record_kind`
- `scope`
- `status`
- `tags`
- `source_refs`
- `confidence`
- `task_id`
- `branch`

编译输出初步划分为：

- `runtime digest`
- `task handoff`
- `system digest`
- `publish queue`

这意味着当前的 `activeContext.md`、`progress.md` 等文件，在长期演进里更适合作为“编译视图”或“人类可读摘要”，而不是唯一主真源。

#### 5. 正式引入候选治理与发布流

本次设计稿把系统记忆与 skill 治理流程统一定义为：

`raw -> candidate -> validated -> published -> degraded/archive`

同时明确以下边界：

- 允许自动创建候选、归档建议、编译视图
- 不允许 LLM 自动发布正式系统记忆
- 不允许 LLM 自动发布正式 skill
- 不允许 LLM 自动删除正式系统规则

这让后续 Memory MCP 能从“记录信息”升级到“治理知识”。

#### 6. 无 LLM 兜底能力被提升为硬要求

设计稿明确要求：即使没有 LLM，系统也必须完整支持：

- 原始记录写入
- Front Matter 解析
- 规则校验
- SQLite FTS 检索
- 模板式 digest 编译
- 候选验证与发布流程

换句话说，LLM 在后续架构中的定位是增强器，不是依赖前提。

### 目录模型调整建议

设计稿提出的 vNext 目标目录如下：

```text
memory-bank/
  shared/
  people/{user}/
  candidates/
  archive/
  compiled/
    runtime/
    publish/

.ai-context/{user}/

.ai-memory/
  config.json
  search.db
  events.jsonl
  compile-cache/
  temp/
  backups/
```

这里最重要的不是目录本身，而是职责变化：

- `shared/` 用于已发布系统记忆
- `people/{user}/` 用于个人长期沉淀
- `candidates/` 用于 system / skill 候选池
- `archive/` 用于降级和长期历史
- `compiled/` 用于运行时和发布流程所需的派生视图

### 与 v0.4.0 的关系

这次设计稿不是否定 `v0.4.0`，而是把 `v0.4.0` 放在更清晰的阶段定位上：

- `v0.4.0` 解决的是“多人协作下文件级记忆系统如何可用”
- 2026-04-21 设计稿解决的是“记忆系统如何从文件工具演进为长期治理系统”

因此后续实施应遵循以下顺序：

1. 先保留现有文件级接口，避免破坏当前工作流
2. 在此基础上增加记录级 schema、Front Matter 解析和 SQLite FTS 索引
3. 再补齐 compile / validate / publish / archive 能力
4. 最后才接入本地小模型或云端 LLM 做增强能力

### 对后续开发的直接约束

根据本次设计稿，后续继续开发 `ToolTest/MCP/Memory` 时应遵守以下约束：

1. 不再把少量共享 Markdown 文件当作唯一真源
2. 新能力优先围绕“记录级 schema”设计，而不是继续堆文件级特判
3. 所有 compile 结果必须可重建，不得成为唯一真源
4. 系统记忆正式发布必须经过候选验证，不允许 LLM 直接跳过治理流程
5. 任何增强能力都不得阻断无 LLM 的基础链路

### 产出

- 新增设计文档：`MemorySystemDesignDocument.md`
- 文档用途：作为 `MCP/Memory` 后续开发的 vNext 架构基线
- README 应继续维护“当前实现说明”，设计文档负责维护“下一阶段架构设计”

---

## 2026-03-31 (v0.4.0 设计稿: 十人团队多人协作 — 用户分区写入 + 冲突消除)

> **状态：设计方案，未执行开发**

### 背景与问题

团队即将扩展至 10 人规模，当前 Memory MCP 的多人协作支持存在以下瓶颈：

| # | 问题 | 严重度 | 影响范围 |
|---|------|--------|----------|
| 1 | `activeContext.md` 使用 overwrite 模式，10 人同时写入必然产生全文 Git 冲突 | **P0** | 每次 push/pull |
| 2 | overwrite 模式不注入任何用户标签，无法区分内容归属 | **P1** | 代码审查、冲突解决 |
| 3 | `.ai-context/` 不进 Git 但也无用户隔离，同一机器多人共用时互相覆盖 | **P1** | 共享开发机 |
| 4 | `config.json` 中 `preferred_mode: "append"` 在 DEVLOG v0.3.0 中提及但代码未实现 | **P2** | 配置与行为不一致 |
| 5 | 全局预算 60K chars 在 10 人场景下可能不够 | **P2** | 写入被拒绝 |

### 当前架构分析

```
memory-bank/              ← 进 Git，全团队共享
  activeContext.md         ← overwrite 模式，冲突高发区
  progress.md              ← 低频写入，冲突风险低
  techContext.md            ← 低频写入，冲突风险低
  systemPatterns.md         ← 低频写入，冲突风险低
  projectbrief.md           ← 极低频，冲突风险极低

.ai-context/              ← 不进 Git，个人临时上下文
  current-task.md          ← 个人任务，不冲突
  latest-error.md          ← 个人错误，不冲突

.ai-memory/               ← config.json 进 Git，其余不进
  config.json              ← 共享配置
  events.jsonl             ← 不进 Git
  backups/                 ← 不进 Git
  temp/                    ← 不进 Git
```

**核心矛盾**：`activeContext.md` 是写入最频繁的文件（每次会话结束都写），但使用 overwrite 模式，10 人 = 10 个 AI 会话 = 高频全文替换 = Git 冲突地狱。

### 设计方案

#### 总体策略：按用户分文件 + 共享文件只读/append

```
memory-bank/
  activeContext/                    ← 新：目录替代单文件
    _shared.md                      ← 团队共享上下文（只读 or append-only）
    mengzhoyang.md                  ← 个人活跃上下文
    zhangsan.md                     ← 个人活跃上下文
    ...                             ← 每人一个文件，最多 10 个
  progress.md                       ← 保持不变（低频，append 模式）
  techContext.md                    ← 保持不变（低频，append 模式）
  systemPatterns.md                 ← 保持不变（低频，append 模式）
  projectbrief.md                   ← 保持不变（极低频）
```

#### 改动 1：activeContext 从单文件改为用户分区目录

**原理**：每人写自己的文件，Git 合并零冲突。

| 操作 | 旧行为 | 新行为 |
|------|--------|--------|
| 会话开始读取 | `memory_get("memory-bank/activeContext.md")` | `memory_get("memory-bank/activeContext/{user}.md")` + `memory_get("memory-bank/activeContext/_shared.md")` |
| 会话结束写入 | `memory_write("memory-bank/activeContext.md", ..., mode="overwrite")` | `memory_write("memory-bank/activeContext/{user}.md", ..., mode="overwrite")` |
| 团队公告/决策 | 无 | `memory_write("memory-bank/activeContext/_shared.md", ..., mode="append")` |

**`{user}` 来源**：`get_current_user(config.repo_root)` — 已有实现，优先读 `.vscode/settings.json["memory-mcp.userName"]`。

**实现要点**：
- `memory_writer.py`：新增 `resolve_user_path(path, user)` 函数
  - 当 path 匹配 `memory-bank/activeContext.md` 时，自动重定向到 `memory-bank/activeContext/{user}.md`
  - 其他路径不受影响
- `memory_reader.py`：`memory_get` 同理，读取时自动重定向
- `server.py`：工具描述更新，说明 activeContext 的用户分区行为
- 向后兼容：如果旧的 `activeContext.md` 单文件仍存在，首次启动时自动迁移到 `activeContext/{user}.md`

#### 改动 2：overwrite 模式注入用户标签尾注

**原理**：即使是个人文件，也需要可追溯性。

```python
# memory_writer.py — overwrite 分支
else:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    footer = f"\n<!-- last overwritten by {current_user} at {timestamp} -->\n"
    final_content = content.rstrip("\n") + "\n" + footer
```

**影响**：所有 overwrite 写入的文件末尾都会有 `<!-- last overwritten by xxx at xxx -->` 标签。

#### 改动 3：共享文件强制 append + 用户标签

**原理**：`progress.md`、`techContext.md`、`systemPatterns.md` 是团队共享知识，多人写入应使用 append 模式。

**实现**：
- `config.json` 的 guard targets 新增 `write_policy` 字段：

```json
{
  "path": "memory-bank/progress.md",
  "write_policy": "append_only",
  "role": "feature completion status, milestones"
}
```

- `memory_writer.py`：写入前检查 target 的 `write_policy`
  - `"append_only"`：如果调用方传入 `mode="overwrite"`，自动降级为 `append` 并在返回中标注 `"policy_override": "append_only"`
  - `"user_scoped"`：触发改动 1 的用户分区逻辑
  - `null` / 不设置：保持当前行为

#### 改动 4：`.ai-context/` 用户隔离（共享开发机场景）

**原理**：`.ai-context/` 不进 Git，但同一机器多人共用时会互相覆盖。

```
.ai-context/
  mengzhoyang/
    current-task.md
    latest-error.md
  zhangsan/
    current-task.md
    latest-error.md
```

**实现**：与改动 1 类似，`resolve_user_path` 对 `.ai-context/` 下的路径也做用户分区。

**注意**：这个改动只在共享开发机场景下有意义。如果每人独立机器，`.ai-context/` 天然隔离，此改动可延后。

#### 改动 5：全局预算扩容

| 参数 | 当前值 | 10 人建议值 | 理由 |
|------|--------|-------------|------|
| `total_max_chars` | 60,000 | 150,000 | 10 人 × 8K activeContext + 共享文件 |
| `total_max_tokens` | 15,000 | 40,000 | 同比例扩容 |
| `guard_default_max_chars` | 12,000 | 12,000 | 单文件上限不变 |
| 每人 activeContext 上限 | — | 6,000 chars | 新增 per-user guard target |

#### 改动 6：config.json 新增结构

```json
{
  "multi_user": {
    "enabled": true,
    "user_scoped_paths": [
      "memory-bank/activeContext.md",
      ".ai-context/current-task.md",
      ".ai-context/latest-error.md"
    ],
    "shared_paths_policy": {
      "memory-bank/progress.md": "append_only",
      "memory-bank/techContext.md": "append_only",
      "memory-bank/systemPatterns.md": "append_only",
      "memory-bank/projectbrief.md": "append_only"
    }
  },
  "guard": {
    "total_max_chars": 150000,
    "total_max_tokens": 40000,
    "per_user_max_chars": 6000,
    "targets": [
      {
        "path": "memory-bank/activeContext/{user}.md",
        "max_chars": 6000,
        "write_policy": "user_scoped",
        "role": "per-user sprint focus and recent decisions"
      },
      {
        "path": "memory-bank/activeContext/_shared.md",
        "max_chars": 8000,
        "write_policy": "append_only",
        "role": "team-wide announcements, shared decisions"
      }
    ]
  }
}
```

### 迁移计划

```
Phase A — 基础设施（无破坏性变更）
  1. memory_writer.py: 实现 overwrite 尾注（改动 2）
  2. memory_config.py: 新增 multi_user / write_policy 配置解析
  3. memory_writer.py: 实现 write_policy 检查（改动 3）
  4. 测试：全部原有测试通过 + 新增 write_policy 测试

Phase B — 用户分区（核心改动）
  5. memory_writer.py + memory_reader.py: 实现 resolve_user_path（改动 1）
  6. server.py: 工具描述更新
  7. config.json: 新增 multi_user 配置（改动 6）
  8. 迁移脚本: activeContext.md → activeContext/{user}.md
  9. 测试：用户分区读写 + 迁移 + 向后兼容

Phase C — 扩容与可选改动
  10. config.json: 全局预算扩容（改动 5）
  11. .ai-context 用户隔离（改动 4，可选，视是否有共享开发机需求）
  12. copilot-instructions.md: 更新 Memory Bank 硬规则中的路径说明
```

### Git 冲突分析（改动后）

| 文件 | 写入频率 | 写入模式 | 冲突概率 |
|------|----------|----------|----------|
| `activeContext/{user}.md` | 高（每次会话） | overwrite | **零**（每人独立文件） |
| `activeContext/_shared.md` | 低（团队决策时） | append | **极低**（append 天然可合并） |
| `progress.md` | 低 | append | **极低** |
| `techContext.md` | 低 | append | **极低** |
| `systemPatterns.md` | 低 | append | **极低** |
| `projectbrief.md` | 极低 | append | **几乎为零** |

### 风险与注意事项

1. **AI 指令适配**：`copilot-instructions.md` 中的 Memory Bank 硬规则需要同步更新路径（`activeContext.md` → `activeContext/{user}.md`），否则 AI 会话仍会尝试写入旧路径
2. **向后兼容**：需要处理旧的 `activeContext.md` 单文件到新目录结构的迁移，建议首次检测到旧文件时自动迁移
3. **用户名一致性**：10 人团队必须确保每人的 `.vscode/settings.json` 中配置了唯一的 `memory-mcp.userName`，否则会出现用户名冲突。建议在 `memory_writer.py` 中增加用户名校验（非空、非 unknown）
4. **compaction 适配**：`memory_compact` 的 `warm_context` 策略需要适配目录结构，对每个用户文件独立执行 compaction
5. **memory_search 适配**：搜索范围需要包含 `activeContext/` 目录下的所有用户文件
6. **guard 适配**：per-user guard target 需要动态匹配 `{user}` 占位符

### 工作量估算

| Phase | 改动文件数 | 预估工时 | 优先级 |
|-------|-----------|----------|--------|
| A（基础设施） | 2-3 | 2h | 高 |
| B（用户分区） | 4-5 | 4h | 高 |
| C（扩容与可选） | 2-3 | 2h | 中 |
| 测试 | 1-2 | 2h | 高 |
| 文档更新 | 2 | 1h | 中 |
| **合计** | | **~11h** | |

---

## 2026-03-24 (v0.3.1: 用户名可配置覆盖 — .vscode/settings.json)

### 背景
v0.3.0 的用户标识完全依赖 OS 用户名（`USERNAME`/`USER` 环境变量），但存在以下场景需求：
1. 多人共用同一台机器/同一 OS 账户时无法区分
2. 用户希望使用自定义名称（如昵称、工号）而非系统用户名
3. 需要一个**不进 Git、不影响 VSCode 本身**的本地配置方式

### 实现

#### 1. `.vscode/settings.json` 用户名覆盖 (`memory_events.py`)
- `get_current_user()` 新增可选参数 `repo_root: Path | None`
- 优先级变为：`.vscode/settings.json["memory-mcp.userName"]` → `USERNAME` → `USER` → `'unknown'`
- 新增 `_read_vscode_username(repo_root)` 内部函数，带进程级缓存（同一 repo_root 只读一次文件）
- 文件不存在、解析失败、key 不存在时静默回退，零副作用

#### 2. 调用方适配 (`memory_events.py` + `memory_writer.py`)
- `append_event()` 调用 `get_current_user(config.repo_root)` 传入项目根目录
- `memory_writer.py` 中 `get_current_user(config.repo_root)` 同步适配

#### 3. 配置示例
`.vscode/settings.json`（已被 `.gitignore` 排除，不进 Git）：
```json
{
    "memory-mcp.userName": "mengzhoyang"
}
```
- VSCode 对未知 key 完全忽略，不影响 IDE 本身行为
- 每位开发者可在本地自定义用户名

### Verification
- `pytest tests/ -v` → 44 passed（全部原有测试通过，向后兼容）
- 不传 `repo_root` 时行为与 v0.3.0 完全一致

### 影响
- 向后兼容：`get_current_user()` 无参调用仍返回 OS 用户名
- `.vscode/` 目录已在 `.gitignore` 中排除，配置不进 Git
- 进程级缓存避免频繁文件 I/O

---

## 2026-03-24 (v0.3.0: 多人协作支持 — 用户标识 + Git 共享策略)

### 背景
多人同一项目使用 Memory MCP 时存在三个问题：
1. 审计日志不记录操作者，无法追溯谁写了什么
2. `events.jsonl`、`backups/`、`temp/` 等运行时基础设施文件进 Git 会产生无意义冲突
3. `activeContext.md` 使用 overwrite 模式，多人写入必然产生全文 Git 冲突

### 实现

#### 1. 用户自动识别 (`memory_events.py`)
- 新增 `get_current_user()` 工具函数
- 读取 `USERNAME`（Windows）/ `USER`（POSIX）环境变量，完全无感无需配置
- 未找到时回退到 `'unknown'`

#### 2. 审计日志注入用户 (`memory_events.py`)
- `append_event()` 记录自动注入 `"user"` 字段
- 所有写操作（write、backup、compact）的审计记录均可追溯操作者

#### 3. append 模式用户标识头 (`memory_writer.py`)
- 当 `mode == "append"` 时，自动在追加内容前插入 HTML 注释标识头：
  `<!-- written by {user} at {timestamp} -->`
- 多人追加不冲突，且可追溯每段内容的作者

#### 4. Git 共享策略 (`.gitignore`)
- `memory-bank/` 全部进 Git（全团队共享项目记忆）
- `.ai-memory/config.json` 进 Git（共享配置）
- `.ai-memory/events.jsonl`、`backups/`、`temp/` 排除出 Git（运行时基础设施）
- `.ai-context/` 维持不进 Git（个人临时上下文）

#### 5. 配置标注 (`memory_config.py`)
- `activeContext.md` 的 guard target 新增 `preferred_mode: "append"` 字段
- 作为多人协作时的推荐写入模式标注

### Verification
- `pytest tests/ -v` → 44 passed（原 44 全部通过，无破坏性变更）
- `get_current_user()` 在 Windows / POSIX 环境均可正常获取用户名

### 影响
- 向后兼容：`user` 字段为新增，旧审计记录无此字段不影响解析
- `preferred_mode` 为提示性字段，不改变实际写入逻辑
- `.gitignore` 变更不影响已有 Git 历史

---

## 2026-02-25 (v0.2.0: 备份轮转 + 全局预算 + 动态描述)

### 背景
P3 遗留问题：备份无限增长、无全局记忆预算、工具描述硬编码无法跟随配置变化。

### 实现

#### 1. 备份轮转 (`memory_backup.py`)
- 新增 `_list_batches()` / `_dir_size()` / `_rotate_backups()` 辅助函数
- 每次 `backup_files()` 执行后自动调用轮转
- 两重限制：`max_batches`（默认 50）+ `max_total_bytes`（默认 50MB）
- 先按 batch 数裁剪，再按总大小裁剪，从最旧开始删除
- 清理空日期目录

#### 2. 全局记忆预算 (`memory_guard.py` + `memory_writer.py`)
- `memory_guard_check` 返回新增 `total_budget` 字段：总 chars/tokens + 状态 + 消息
- 新增 `check_total_budget()` 可复用函数：预估写入后总量是否超限
- `memory_write` 写入前调用 `check_total_budget(extra_chars=net_new_chars)`
  - 超限 → 返回 `total_budget_exceeded` 错误，拒绝写入
  - 默认全局预算：60000 chars / 15000 tokens

#### 3. 动态工具描述 (`server.py`)
- 拆分为"静态基础描述"（`_BASE_DESCRIPTIONS`）+ "动态文件角色"（从 config 读取）
- `_build_file_roles(config)` 从 guard targets 的 `role` 字段组装文件说明
- `_build_tools(config)` 在 `create_server` 时按需生成 Tool 定义
- 移除全局 `TOOLS` 常量（160+ 行硬编码），改为按 config 动态生成
- 版本号升级至 `0.2.0`

#### 4. 配置文件扩展 (`memory_config.py` + `.ai-memory/config.json`)
- `GuardTarget` 新增 `role: str | None` 字段
- `MemoryConfig` 新增：`guard_total_max_chars`, `guard_total_max_tokens`, `backup_max_total_bytes`, `backup_max_batches`
- `DEFAULT_CONFIG_CONTENT` 新增 `backup` 节点 + `guard.total_max_*` + target `role`
- 配置默认值：备份 50 batch / 50MB，全局 60K chars / 15K tokens

### config.json 新增结构
```json
{
  "backup": {
    "max_total_bytes": 52428800,
    "max_batches": 50
  },
  "guard": {
    "total_max_chars": 60000,
    "total_max_tokens": 15000,
    "targets": [
      {"path": "...", "role": "file description for AI tool hints"}
    ]
  }
}
```

### Verification
- `pytest tests/ -v` → 42 passed（原 30 + 新 12）
- 新增测试覆盖：备份轮转（batch数/总大小/无需轮转/触发轮转）、全局预算（guard返回/通过/超限/写入拒绝/写入通过）、动态描述（role 组装/6工具生成/path hints）

### 影响
- 向后兼容：`role`/`backup`/`total_max_*` 缺省时功能不变
- 破坏性变更：移除全局 `TOOLS` 常量（外部若直接 `from server import TOOLS` 将失败，应改用 `_build_tools(config)`）

---

## 2026-02-25 (新增 memory_write 工具)

### 背景
评估发现 Phase 1 缺少写入工具，AI 无法通过 MCP 直接更新记忆文件。

### 实现
- 新增 `memory_writer.py`：受控写入工具，支持 overwrite / append 两种模式
- 安全特性：
  - `allowed_roots` 路径白名单（PathManager 强制）
  - 写入前自动备份（可关闭）
  - 原子写入（temp file + `os.replace`）
  - 每次写入记录审计事件到 `events.jsonl`
  - 写入后自动 guard 检查，返回 `guard_warning`（容量超阈值时）
- 注册为第 6 个工具，TOOLS 列表更新
- 新增 13 个测试用例覆盖：overwrite / append / 安全拒绝 / 备份控制 / guard 告警 / 审计日志 / 创建新文件 / 尾部换行

### Verification
- `pytest MCP/memory/tests/memory_server/ -v` → 20 passed (原 7 + 新 13)
- `from servers.memory_server.server import TOOLS` → 6 个工具全部注册

### 影响
- 工具数从 5 → 6
- 无破坏性变更，原有 5 个工具接口不变

---

## 2026-02-25 (健壮性审查 & 修复)

### 审查发现
对全部 11 个源文件进行代码审查，发现以下问题：

| 优先级 | 编号 | 问题 | 文件 |
|---|---|---|---|
| P0 | #3 | `memory_guard` 读文件未 catch OSError，单个坏文件中断全部检查 | memory_guard.py |
| P1 | #1 | `events.jsonl` 并发写入无锁保护，可能损坏审计日志 | memory_events.py |
| P1 | #7 | `_terms()` 正则只匹配 ASCII，中文关键词被忽略 | memory_search.py |
| P1 | #5 | Token 估算固定 `chars/4`，中文严重低估 | token_estimator.py |
| P2 | #2 | `memory_backup` 部分失败时已复制文件不回滚、不报告 | memory_backup.py |
| P2 | #6 | `memory_search` 只返回单行 snippet，缺上下文窗口 | memory_search.py |
| P3 | #4 | `_dispatch_tool` 缺参时传空字符串，错误信息不精确 | server.py |
| P3 | #10 | `_ensure_layout` 目录创建顺序冗余 | memory_config.py |

### 修复内容
- **memory_guard.py**: 在 `read_text` 外层加 try-catch `OSError`，标记 target 为 error 后 continue
- **memory_events.py**: Windows 使用 `msvcrt.locking`、POSIX 使用 `fcntl.flock` 做文件锁保护
- **memory_search.py**: `_terms()` 增加中文 Unicode 字符类 `[\u4e00-\u9fff]+`；search 返回匹配行 ±2 行上下文窗口并合并相邻命中
- **token_estimator.py**: 区分中文字符（按 ×0.6）和 ASCII（按 /4）分别估算
- **memory_backup.py**: 改为先校验所有路径、再批量复制；失败时返回 partial_success 附带已成功项
- **server.py**: `_dispatch_tool` 增加 required 参数缺失检测，返回精确错误
- **memory_config.py**: 修正 `_ensure_layout` 目录创建顺序

### Verification
- `run_memory_all_tests.ps1` 全部通过

---

## 2026-02-25 (hotfix: MCP SDK 迁移)

### Problem
- VS Code MCP 客户端无法发现 `project-memory-mcp` 的 5 个工具。
- 根因：服务器使用自定义 raw JSON-RPC stdio 循环实现协议握手，与 VS Code MCP 客户端（基于 `mcp` Python SDK 的 stdio 传输）不兼容。
- 对比：同项目中 `ue-editor-mcp` 使用 `mcp.server.Server` + `mcp.server.stdio.stdio_server`，工具发现正常。

### Fix
- 重写 `servers/memory_server/server.py`：
  - 移除自定义 `_read_message` / `_write_message` / `MemoryToolDispatcher` / `run_server` 等 raw JSON-RPC 代码。
  - 改用 `mcp.server.Server` + `mcp.server.stdio.stdio_server`（与 `ue-editor-mcp` 一致）。
  - 工具定义从 `ToolSpec` dataclass 改为 `mcp.types.Tool`。
  - 业务逻辑（5 个工具的 dispatch）保持不变。
- 更新 `requirements.txt`：添加 `mcp>=1.20`。
- 安装依赖：`python -m pip install mcp` → `mcp==1.26.0`。

### Verification
- 导入测试：`from servers.memory_server.server import TOOLS` → 5 个工具全部注册。
- stdio 集成测试：`initialize` → `tools/list` → `tools/call memory_guard_check` → 全部成功。

### Impact
- `.venv` 新增 `mcp` SDK 及其依赖（pydantic, anyio, httpx 等）。
- 无业务逻辑变更，所有 5 个工具功能不受影响。

---

## 2026-02-25

### Summary
- Completed Phase 1 memory MCP server delivery under `MCP/memory/`.
- Consolidated deployment and test scripts for local operations.
- Synced IDE and Codex MCP configurations to the new `MCP/memory` layout.

### Implemented
- MCP tools:
  - `memory_get`
  - `memory_search`
  - `memory_guard_check`
  - `memory_backup`
  - `memory_compact` (rule-based, safe default `dry_run=true`)
- Server packaging:
  - `servers/memory_server` as module entry (`-m servers.memory_server`)
- Deployment:
  - root deploy script `MCP/memory/deploy.ps1` (venv-first)
  - script-based deploy/run flow in `MCP/memory/scripts/`
- Tests:
  - split tests under `MCP/memory/tests/memory_server/`
  - per-feature test scripts and full test script

### Verification
- `powershell -ExecutionPolicy Bypass -File MCP/memory/scripts/run_memory_all_tests.ps1`
  - result: `7 passed`
- MCP runtime check:
  - initialize: success
  - tool call `memory_guard_check`: `ok=true`

### Notes
- Scope remains Phase 1 only (no vector DB, no embeddings, no FTS, no watcher, no LLM compression).
- Markdown memory files remain source of truth (`memory-bank`, `.ai-context`).
