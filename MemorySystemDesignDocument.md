# MCP 记忆系统设计文档

> 状态：v0.11.1 会话底。P0/P1/P2、P3 结构升级、P3 hardening、P4 LLM pipeline、P4-B `memory_enhance` facade、P4-C 关键文档可重建、P5 RAG Phase 1–2c、P4 LLM 软增强余项（v0.10.0）、v0.10.1 团队接入、v0.11.0 RAG/LLM 首批、v0.11.1 两个保留项（`PRESETS` sha256 + `llm_smoke.py`）均已落地。
>
> 日期：2026-04-30
>
> 配套文档：实现细节与版本流水见 [README.md](./README.md) 与 [DEVLOG.md](./DEVLOG.md)；原始设计稿备份见 `MemorySystemDesignDocument.md.bak`。

## 0. 当前基线（一句话总结）

仓库已具备：4 个 facade tool（`memory_read` / `memory_write` / `memory_context` / `memory_enhance`）+ 24 个 legacy/admin（默认隐藏）+ CLI（`v0.5.10`）+ 三层目录（`memory-bank/` / `.ai-context/` / `.ai-memory/`）+ 路径安全 + 原子写入 + 跨进程文件锁 + SQLite FTS（含 CJK bigram/trigram）+ schema v2 + 时间快照 + lineage + importance scoring + budget-first retrieval + dao/fa/shu 视图 + LLM map-reduce pipeline + 6 项 read-only LLM 增强能力 + verified embedding presets + gated 真 LLM smoke。详细历史见 DEVLOG。

## 1. 文档目标

- 用户/AI 只写 raw，关键文档由系统自动重建（无感原则）
- 个人长期沉淀 + 协作交接 + 输出"小而准"重要记忆供 LLM 复用
- 全文检索与历史追溯，与 Git 工作流兼容
- 通过 MCP 暴露统一接口；无 LLM 时基础链路完整可用

## 2. 核心设计原则

### 2.0 无感原则（最高优先）

| 层 | 谁写 | 是否可改 | 例子 |
|---|---|---|---|
| **raw 真源层** | 人类 / agent / 工具 | 写一次冻结（`immutable=True`） | observation / decision / note / incident / claim / rule |
| **派生关键文档层** | 系统自动重建 | 整文件可重建可丢弃 | `activeContext.md` / `progress.md` / `techContext.md` / `systemPatterns.md` / runtime digest |

三档重建降级：`LLM` → `本地小模型 / embedding 模板` → `deterministic`；任一失败自动跌落，全部失败时从 `compiled/snapshots/<doc>-<timestamp>.md` 回退；raw 始终不受损。

人工只在两种场景介入：以 `human` 身份新增 raw（修正/补充）；手动微调关键文档（下次 rebuild 前会自动归档到 `archive/manual-edits/`）。

### 2.1 真源不可变（Raw-Immutable）

- `raw`：`immutable=True` + `authoritative=True`，任何 LLM 不得修改或删除
- `distilled`：`immutable=False` + `derived_from=[raw_id, ...]`，可被任意 LLM 自动重写
- 实现层硬守卫：`assert_raw_writable()` 命中 raw 立即抛 `RawImmutableError`

### 2.2 其它原则（要点）

- **分层**：个人记忆 / 系统记忆 / 历史记忆 / 本地临时记忆 / 编译记忆
- **编译**：从结构化记录筛选 → 路由聚合 → 生成视图；以确定性为主，LLM 仅做软增强
- **预算优先**：先确定 `max_tokens`/`max_chars`/`max_items`，再在预算内选最重要内容；同一条记忆正文不在多 section 重复展开
- **自动化边界**：插件负责"动态记忆供给"，不在插件内维护项目规则真源
- **降级**：无 LLM / 本地小模型 / 云端 LLM 三档运行；LLM 不可用不能阻断基础读写、检索、编译

## 3. 系统边界

| 负责 | 不负责 |
|---|---|
| 记忆写入、结构化记录管理、标签分类、编译运行时视图、检索索引、重要记忆评分/保留/压缩、Git 兼容存储、MCP 暴露 | 替代项目文档/issue/代码仓库；维护项目规则真源；让 LLM 自动决定正式系统共识；让 LLM 直接发布正式 skill |

## 4. 记忆分层

| 层 | 用途 | 特点 |
|---|---|---|
| 个人记忆 | 个人事项、阶段结论、踩坑、交接 | 每人独立、进 Git、允许逐步沉淀 |
| 系统记忆 | 已验证规则、跨人共识 | 数量少、内容稳定、走 candidate→validated→published（兼容路径） |
| 历史记忆 | 归档结论、候选历史、事件日志 | 不默认注入，可检索可重编 |
| 本地临时 | 当前会话缓存、调试信息 | 不进 Git、生命周期短 |
| 编译记忆 | runtime digest / handoff / system digest / **关键文档**（`activeContext.md` 等） | 三档渲染器生成、可重建可丢弃；头部带 `<!-- generated_by=memory-mcp ... -->` |

## 5. 目录结构

```text
memory-bank/
  shared/                       # 已发布系统记忆
  people/{user}/                # 个人记忆
  candidates/                   # 系统/skill 候选池
  archive/                      # 降级与归档
  compiled/
    runtime/                    # system-digest / people/{user}-digest / task / branch
    publish/                    # *-candidates.jsonl
    snapshots/                  # 关键文档历史快照（rebuild fallback）

.ai-context/{user}/             # 本地临时记忆（不进 Git）

.ai-memory/                     # 不进 Git
  config.json                   # 配置（完全可选）
  search.db                     # SQLite FTS 派生索引
  events.jsonl                  # 事件日志
  compile-cache/  temp/  backups/  locks/
  ue_facets.json                # P1-2: UE facet 自动推断词典
  baseline.json                 # P2-1: 规模性能基线
  last_maintenance.json         # P0-3: auto-maintenance 时间戳
```

## 6. 存储格式

- **传输**：JSON（MCP 调用）
- **正文**：Markdown
- **落盘**：Markdown + YAML Front Matter
- **派生索引**：SQLite FTS5（CJK bigram/trigram 兜底，无新增依赖）
- **事件流**：JSONL

记录统一外壳字段（节选）：`schema_version` / `id` / `record_kind` / `scope` / `status` / `author` / `created_at` / `updated_at` / `tags` / `confidence` / `source_refs` / `task_id` / `branch` / `validated_by` / `last_used_at`。完整 schema v2 字段（occurred_at / valid_from-to / memory_tier / cognitive_level / derived_from_* / supersedes / conflicts_with / importance_score / facet 字段）见代码 `memory_records.py` 与 README §3.4。

记录类型：`note` / `event` / `claim_candidate` / `rule_candidate` / `handoff` / `skill_candidate` / `validation_result` / `system_rule` / `archive_record` / `observation` / `artifact_ref` / `incident` / `decision` / `procedure` / `snapshot_daily|weekly|monthly`。

标签为受控词表，只做路由不做真相判定。

## 7. 写入模型

- **硬元数据**（系统/调用方决定）：`author` / `created_at` / `source` / `task_id` / `branch` / `workspace` / `event_id` / 默认 `status` / 文件路径
- **软元数据**（LLM 可在 schema 内填）：`record_kind` / `tags` / `confidence` / `scope_hint` / `skill_possible` / `needs_validation`

写入边界（**raw 永远不能改、distilled 可以随便改**）：

- LLM **不能**：改/删 raw、把 distilled 提升为 raw、产出无 `derived_from` 的 distilled
- LLM **可以（无需人工确认）**：写新 raw（立即冻结）、写新 distilled、`supersede` 旧 distilled、自动归类/打标/生成 abstract/snapshot narrative

历史 `published` / `validated` / `dao` 走兼容路径，仍保留人工/规则限制（见 §10），不再是默认链路。

## 8. 编译模型

编译 = 从结构化记录筛选 → 按规则分组聚合 → 生成视图（不是 LLM 重写总结）。

主要编译目标（均已实现）：

| target | 用途 | 输出 |
|---|---|---|
| `runtime_digest` | 当前 agent 运行时上下文 | `compiled/runtime/` |
| `task_handoff` | 跨会话/交接 | `compiled/runtime/task/{task_id}-handoff.md` |
| `system_digest` | 团队共享 digest | `compiled/runtime/system-digest.md` |
| `publish_queue` | 治理审核入口 | `compiled/publish/publish-queue.md` |
| `daily/weekly/monthly_snapshot` | 时间快照 | `compiled/snapshots/...` |
| `rollback_context` / `review_queue` | 回退/回顾 | `compiled/...` |
| `dao_digest` / `fa_digest` / `shu_digest` | 按 cognitive_level 分层视图 | `compiled/...` |

编译原则：标签只做路由不做真相；优先读结构再读正文；产物可重建（不是唯一真源）；支持增量；默认面向运行时上下文压缩，正文优先抽 `Decision` / `Expected Behavior` / `Acceptance Checks` / `Next Step(s)` 等关键段；`body_mode="full"` 仅审计/调试时使用。

## 9. 检索模型

- **真源**：Markdown + JSONL + Front Matter；**派生索引**：SQLite FTS（CJK bigram/trigram）
- **流程**：解析 Front Matter → 元数据/tag 过滤 → FTS 全文 → 重排
- **预算策略**：入口必须接受 `max_tokens` / `max_chars` / `max_items`；按预算逐条装配（不是简单 `top_k`）；返回必须含 `budget_report` / `dropped_candidates` / `evidence_refs`
- **预筛优化**（v0.5.11）：索引健康时先用 SQLite metadata/facet 缩小候选集，再回 Markdown 真源做确定性排序；索引异常时无损回退全量扫描
- **回退**：标签缺失时仍可按 `scope` / `author` / 时间范围 / 关键词 / 全文检索兜底

## 10. 验证与发布治理（兼容层）

> ⚠️ `candidate -> validated -> published` 是历史兼容流程，不是默认路径。新流程默认走 §2.1 的 raw + distilled。仅历史数据迁移、跨团队共识发布等场景才走此链路。

链路：`raw -> candidate -> validated -> published -> degraded/archive`。

实现要点：

- `memory_validate_candidate` / `memory_publish_candidate` / `memory_archive_record`：临时文件 → `os.replace` → 删旧路径，全程原子
- `memory_delete_record` 只允许删 archived 记录，墓碑写入 `.ai-memory/tombstones.jsonl`
- 多人模式默认开启：`activeContext.md` 自动按 `{user}.md` 分流；`progress.md` / `techContext.md` / `systemPatterns.md` / `projectbrief.md` 默认 append-only（旧配置无 `write_policy` 也通过 `multi_user.user_scoped_paths` / `shared_paths_policy` 兜底）
- 维护工具：`memory_health_check` / `memory_migrate_records` / `memory_update_index`
- 安全加固（v0.4.1 起，v0.5.4-0.5.6 加强）：`O_CREAT|O_EXCL|O_WRONLY` + fsync + `os.replace` + `FILE_SHARE_DELETE` 重试 + 长路径 `\\?\` 规范化 + `DiskFullError` 结构化错误 + `events.jsonl` 滚动归档；详见 DEVLOG

## 11. MCP 接口

### 11.1 默认 4 个 facade

| Facade | operation | 用途 |
|---|---|---|
| `memory_read` | `get` / `search` / `search_records` / `runtime_digest` | 读取、搜索、读 digest |
| `memory_write` | `file` / `record` / `observation` / `link_artifact` | 写文件、结构化记录、observation、关联 artifact/facet；可选 `distill=true` 触发蒸馏 |
| `memory_context` | `compile` / `runtime_digest` / `trace_lineage` / `list_conflicts` / `compare_snapshots` / `retrieve_context` / `important_memories` / `config_diagnose`(P1-4) | 上下文装配、编译、谱系、冲突、snapshot 对比、重要记忆输出；可选 `summarize=true` 召回概括 |
| `memory_enhance` | `classify_record` / `extract_candidates` / `merge_candidates` / `generate_skill_candidate` / `explain_conflict` / `generate_handoff` | read-only LLM 软增强，永不写盘 |

管理类能力（guard / backup / compact / index / health / migrate / governance / snapshot rebuild / conflict review）默认不暴露为 MCP，迁移到 CLI（`servers/memory_server/cli.py`）+ skill（`memory-admin` / `memory-snapshot-review`）。开发期可显式 `mcp.expose_admin_tools=true` 暴露完整 24 个工具。

### 11.2 LLM 接入（OpenAI 兼容协议）

实现位置：`memory_llm.py` + `memory_llm_pipeline.py` + `memory_llm_enhance.py` + `memory_llm_policy.py`。

- 默认 profile：DeepSeek（`https://api.deepseek.com`，`deepseek-chat`），任何兼容服务（OpenAI / Moonshot / vLLM / Ollama 网关）切换 `base_url`+`model` 即可
- 配置优先级：显式 `LLMConfig` > 环境变量（`MEMORY_LLM_API_KEY` / `BASE_URL` / `MODEL` / `TIMEOUT`，回落 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`）> `MCP/Memory/llm_config.local.json`（**不入 Git**）
- 仅依赖 stdlib（`urllib`），可注入 transport 便于 mock
- LLM 失败抛 `LLMRequestError`，调用方退化为无 LLM 路径
- pipeline 提供 `compute_distill_cache_key` / `chunk_raw_records` / `map_reduce_distill` / `summarize_records_for_recall`，处理"同输入不重复花钱"+"过长输入分块汇总"
- 主路径接入：`memory_write(distill=true)` raw 落盘后蒸馏；`memory_context(summarize=true)` 召回结果概括；二者均 opt-in，未触发即 0 LLM 调用 / 0 token

⚠️ 安全：`llm_config.local.json` 与 `*.api_key` / `*.apikey` 已在 `MCP/Memory/.gitignore`，禁止把 API key 提交版本库。

## 12. LLM / 非-LLM 职责划分

判断标准：(1) 非 LLM 已经做得足够好的事不引入 LLM；(2) 离开 LLM 做不好的事由 LLM 做并落 distilled 层。

| 能力 | Owner | 备注 |
|---|---|---|
| raw 写入 / 哈希 / 冻结 | **非 LLM** | `memory_writer` + `make_raw_record` |
| Front Matter 解析 / Schema 校验 | **非 LLM** | 规则即可 |
| 事件日志 / Lock / Backup / Compactor | **非 LLM** | deterministic |
| 关键词 / FTS 检索 | **非 LLM** | SQLite FTS 已是 BM25 基线 |
| 评分 / 排序 / 衰减 | **非 LLM** | `memory_scoring` |
| Token 估算 | **非 LLM** | `token_estimator`（CJK-aware） |
| 编译模板 digest（无 LLM 兜底） | **非 LLM** | "稳但朴素"路径，永远可用 |
| 自然语言摘要 / 重写 / 蒸馏 | **LLM** | 必须 `derived_from` 指回 raw |
| 跨 raw 主题聚类 / 命名 | **LLM** | 落 distilled，可重做 |
| 冲突识别（语义层） | **LLM 提议 → 非 LLM 校验** | LLM 输出候选；governance 流程裁决 |
| 自然语言查询解析 | **LLM 提议 → 非 LLM 检索** | LLM 翻译为 facets，FTS 执行 |
| 谱系 / `derived_from` 维护 | **非 LLM** | 审计基础 |
| 治理（candidate→...→published） | **非 LLM** | 仅历史兼容 |
| 成本/预算控制 | **非 LLM** | LLM 不能自己决定要不要再花一次钱 |

不变量：

- 任意环节失败/关闭 LLM，系统不退化为 0：raw 仍写入、FTS 仍检索、模板 compile 仍出 digest
- LLM 输出从不直接覆盖 raw 或非 LLM 索引，只产出 distilled
- distilled 可清空可重建，不损坏 raw 真源

代码层单一事实源：`memory_llm_policy.LLM_CAPABILITY_MATRIX` + `should_use_llm(capability)`。新加 LLM 必须先在矩阵登记并加单测。

## 13. Git 策略

- **进 Git**：`memory-bank/shared/` / `memory-bank/people/{user}/` / `memory-bank/candidates/` / `.ai-memory/config.json`
- **不进 Git**：`.ai-context/` / `.ai-memory/`（含 search.db / events.jsonl / backups / temp / compile-cache / locks / *.api_key / llm_config.local.json）

## 14. 权限与冲突策略

- 个人增量提交为主，不鼓励多人共写同一系统正文文件
- 系统记忆走候选汇总与发布；shared 层尽量由发布流程写入
- 共享内容采用追加、分段、生成式汇总；编译产物可重建，不作主编辑层

## 15. 后续计划与收口状态

> 已完成里程碑的实现细节迁移至 [README §5](./README.md#5-开发历史与计划)；版本流水见 [DEVLOG.md](./DEVLOG.md)。本节保留 v0.11.x 收口状态与仍需观察/未触发的后续方向。

### 15.1 【最高优先级】RAG 召回质量解锁（v0.11.x）

> **收口状态（v0.11.1）**：A / B / C / D 四项均 ✅。B 在 v0.11.1 补齐 verified presets；真模型召回基线转为后续观察项。详见 DEVLOG。

**问题诊断**：当前 RAG 通路骨架完整、安全约束扎实（§15.3 完成项 v0.7.5–v0.9.0），但召回**质量**未达生产线。三处具体阻塞：

1. **`LocalOnnxProvider` 的分词器是 byte-level hash 桩件**（[`memory_embeddings.py`](./servers/memory_server/memory_embeddings.py)）：bge-small / MiniLM 这类模型必须配套 WordPiece / BPE / SentencePiece，桩件分词在 ONNX 模型上推出来的向量与"真正训练出来的语义空间"严重失配，导致 cos 分布偏窄、跨条目区分度低；**这是当前设计-实现 gap 最大的一处**。
2. **`scripts/download_embedding_model.py` 的 `PRESETS` sha256 曾为 `<fill-me-in>`**：v0.11.1 已补齐两组 verified preset，下载模型、tokenizer 与必要 sidecar 均强制 sha256 校验。
3. **召回质量没有可回归的评测**：当前只有功能测试（"接入点不抛"），没有 recall@k / MRR / 跨 provider 对比脚本；改 chunk 大小 / 重排权重 / provider 时无法量化好坏。

**验收门**（缺一项不算解锁）：

| 项 | 验收标准 | 状态 |
|---|---|---|
| A. 真实分词器 | `LocalOnnxProvider` 引入 `tokenizers` 或 `sentencepiece`（按模型族二选一），与 `model_path` 同目录读取 `tokenizer.json` / `*.model`；缺失则 provider 走 `unavailable` 而非桩件输出 | ✅ v0.11.0 |
| B. `PRESETS` 哈希 | `bge-small-zh-v1.5` 与 `paraphrase-multilingual-MiniLM-L12-v2` 至少一组 `model_url + tokenizer_url + sha256` 全部填实；脚本 `--list` 必须显示已校验 | ✅ v0.11.1 |
| C. 召回评测脚本 | 新增 `scripts/eval_recall.py`：读 `tests/data/recall_set.jsonl`（人工标注的 query→record_id 列表）→ 跑 `vector_search` / `_rank_records` → 输出 `recall@5/10` + `MRR` + `provider_id` + `model_hash`；CI smoke 跑 deterministic-hash provider 设下限 | ✅ v0.11.0 |
| D. 失败可观测 | `_vector_supplement` 异常路径必须写 `events.jsonl`（type=`vector_supplement_skipped`）而非纯 try/except 吞掉；health 暴露 `vector_skip_count_24h` | ✅ v0.11.0 |

**非目标**：不引入远程 API；不引入 GPU 依赖；不改 §15.3 已完成的索引格式与重建协议。

### 15.2 【最高优先级】LLM 调用统一（v0.11.x）

> **收口状态（v0.11.1）**：A / B / C / D 四项均 ✅。C 在 v0.11.1 补齐 `scripts/llm_smoke.py`。详见 DEVLOG。

**问题诊断**：v0.10.0 已落地 `memory_llm_runner.run_llm_capability` + 七状态包络，但仓库内仍有**双轨调用**：

1. `_run_distill_for_write` / `_run_recall_summarize` / `key_documents` 的 LLM renderer 仍在用旧的 try/except + 临时降级，错误状态无法对齐七状态包络。
2. 缺少 `tests/memory_server/test_dispatch.py` 对 `rewrite_query` / `narrative` 这两条 v0.10.0 入口的 facade 端到端测试（当前只有单元测试）。
3. 没有"真 LLM smoke" 脚本（gated）：所有 LLM 测试都跑 `FakeProvider`，配置改变 / SDK 升级时无法自检。

**验收门**：

| 项 | 验收标准 | 状态 |
|---|---|---|
| A. 三处入口收敛 | `_run_distill_for_write`、`_run_recall_summarize`、`memory_key_documents.render_llm_document` 全部改走 `run_llm_capability(capability=…)`；所有失败必须返回七状态之一 | ✅ v0.11.0 |
| B. facade dispatch 测 | `test_dispatch.py` 增 `rewrite_query` / `narrative` 两个 facade 端到端用例，覆盖 `enabled=False` / `unavailable` / `timeout` / `ok` 四档 | ✅ v0.11.0 |
| C. 真 LLM smoke | `scripts/llm_smoke.py`（gated by `MEMORY_LLM_SMOKE=1` + 真 key），跑 `distill_for_write` + `query_rewrite` + `snapshot_narrative` 三个最小 case，输出每个 capability 的 status / latency / token_used；CI 不跑，开发者手动跑 | ✅ v0.11.1 |
| D. 配置可视化 | `memory_context.config_diagnose` 输出新增 `llm_capabilities` 段，列每个 capability 的 `enabled / provider / timeout_ms / fallback`，按文件 / 环境 / 默认归类来源 | ✅ v0.11.0（5 能力 × 4 字段，每字段 `{value, source}`） |

**非目标**：不替换 deterministic 路径；不引入 LLM 强依赖；`enabled` 默认仍为 `False`。

### 15.3 已完成里程碑速查表

> 详细实现（P0/P1/P2 清单、RAG 各 phase、provider 抽象、索引协议、LLM runner 七状态等）已迁移至 [README §5](./README.md#5-开发历史与计划)。

| 版本 | 主题 | 完成 |
|---|---|---|
| v0.6.0 | OOTB 稳健性（user 强校验 / shared overwrite 拒绝 / auto-maintenance / bootstrap.ps1 / UE facet 自动盘点 / shared compactor / scale-baseline / health 自愈） | OK |
| v0.7.0 | P4-C 关键文档可重建（KEY_DOCUMENTS manifest + facade rebuild_key_documents + CLI + 三档 renderer + backup 强保留） | OK |
| v0.7.5 / v0.8.0 / v0.9.0 | RAG Phase 1 / 2a / 2b / 2c（provider 抽象 + DeterministicHash + 分块编排 + retrieval `_vector_supplement` + key_documents embedding renderer + LocalOnnxProvider CPU EP only + sha256 强校验脚本） | OK |
| v0.10.0 | P4 LLM 软增强收尾（`memory_query_rewrite` + `memory_snapshot_narrative` + `memory_llm_runner` 七状态包络 + `llm_defaults` capability 粒度） | OK |
| v0.10.1 | 团队接入扫尾（`MEMORY_MCP_USER` 最高优先级 user 来源 + README §3.3 失败模式表 + UE Facet §17 文档收口 + 并发/多进程子集 64 用例全绿） | OK |
| v0.11.0 | RAG 召回质量解锁 + LLM 调用统一首批（§15.1-A/C/D + §15.2-A/B/D；621 passed + 3 skipped） | OK |
| v0.11.1 | v0.11.x 保留项收口（§15.1-B verified `PRESETS` + §15.2-C `llm_smoke.py`；624 passed + 3 skipped） | OK |

### 15.4 已降级方向（保留兼容，不再扩张）

- **多人联合项目治理**：现有 validate / publish / archive 链路保留用于历史数据；治理动作迁至 CLI / scripts / 管理 skill；不再把"插件内自动审查与规则晋升"当作主产品方向。

### 15.5 已冻结方向（保留实现，不再扩张）

- **整个 vector / RAG 通路**（`memory_embeddings.py` / `memory_vector_search.py` / `memory_vector_index.py` / `memory_vector_corpus.py` + `_vector_supplement` + key_documents `embedding` renderer）：自 v0.11.x slim-down 起冻结。
  - 现状：默认 `embeddings.enabled=False`，主路径 0 字节、0 调用开销；五个模块文件头与两个消费入口（`memory_retrieval._vector_supplement` / `memory_key_documents._render_embedding_renderer`）均带 `EXPERIMENTAL — FROZEN` 横幅。
  - 不启动条件（任一命中即可解冻）：
    - `chunks ≥ 100k`（当前约 1.5–3 万）
    - 全量重建 `≥ 10 min`（当前 sub-minute）
    - 稳态 `QPS ≥ 20`（当前 < 1）
    - 完成"真模型召回基线"观察项（设计文档 §16）并证明 deterministic + FTS 召回不足
  - **冻结期允许的改动**：保持现有契约的 bug fix；测试维护；`embeddings.enabled=True` opt-in 路径上的兼容性修复。
  - **冻结期不允许的改动**：新 provider；新 chunk 策略；新 renderer；新 tuning knob；HNSW / 量化 / GPU EP（属于已计划但未触发的 RAG Phase 3）。
- **RAG Phase 3**（GPU EP / 量化 / HNSW）：上述阈值任一命中后再考虑。

> 说明：vector tier 既"已落地"又"未达阈值"，因此从原 §15.5「不启动」升级为「已冻结」；治理链路（原 §15.4）维持「已降级，保留兼容」。两者的差别在于：治理链路有历史数据迁移路径需要保留入口；vector tier 是默认 0 消费、可随时无感重新启用。

## 16. 最终结论

- **形式**：MCP（接口）+ JSON（传输）+ Markdown+Front Matter（存储）+ SQLite FTS（索引）+ 可选向量索引（`.ai-memory/vector_index/`）。
- **写入**：raw 冻结由代码生成，distilled 可由任意 LLM 重写，谱系由 `derived_from` 绑死。
- **编译**：确定性为主，LLM/RAG 仅软增强；产物可重建可丢弃。
- **兜底**：无 LLM、无 onnxruntime、无模型时基础写入/检索/快照/评分/编译/关键文档 deterministic 渲染均完整可用。
- **下一阶段观察项**：下载真实 local-onnx 模型后跑 `scripts/eval_recall.py` 建立召回基线；RAG Phase 3 继续按 §15.5 阈值触发。详细历史见 [DEVLOG.md](./DEVLOG.md) 与 [README.md](./README.md)。
## 17. UE Facet 数据模型（已实现，v0.10.1 文档收口）

UE 专属记忆建模分两层：**项目级自动盘点** + **记录级 facet 字段**。两层都是确定性的、可关闭的；非 UE 仓库自动跳过，无任何强制依赖。

### 17.1 项目级自动盘点（`memory_ue_facets.py`）

启动期 / `health` 触发，扫 `repo_root` 写 `.ai-memory/ue_facets.json`：

| 来源 | 抽取字段 |
|---|---|
| `*.uproject` | `project`、`engine_association`、`modules[]` |
| `Source/**/*.Build.cs` | `dependencies[]`（解析 `(Public\|Private)?DependencyModuleNames.Add(Range)?` 数组字面量） |
| `Plugins/**/*.uplugin` | `plugins[]`（`FriendlyName` → `Name` → 文件名 fallback） |

`is_ue_project=true` 当根目录有任意 `*.uproject`；否则全字段为空、不写文件。`known_components(facets)` 暴露已知名集合给 record-write warning 链路（`memory_record_ue_warnings`：`components` 字段含未知名时回 `ue_unknown_components` warning，不阻塞）。

### 17.2 记录级 facet 字段（`memory_records` / `memory_lineage` / `memory_artifact_paths`）

`memory_write(operation="record")` 与 `memory_context` 多个 op 接受同一组 facet：

| 字段 | 命名空间 | 归一化 | 索引/检索 |
|---|---|---|---|
| `asset_paths` | UE `/Game/...` 或物理 `Content/X.uasset` | `normalize_asset_paths` 双向解析 + 附 `git_sha` | FTS + `link_artifact` |
| `blueprint_paths` | 同 `asset_paths`（约定 `/Game/...`） | 同上 | FTS + lineage |
| `map_names` | UE Map / Level / World Partition 名 | string 列表去重 | FTS facet |
| `module_names` | C++ 模块名（与 `Build.cs` 抽取的 `dependencies` 同源） | string 列表去重 | FTS facet + warning |
| `plugin_names` | 插件名（与 `*.uplugin` 同源） | string 列表去重 | FTS facet + warning |
| `class_names` | UCLASS / 普通 C++ class | string 列表去重 | FTS facet |

`memory_context.compile / retrieve_context / important_memories` 在 schema 上原生暴露上述六个字段，作为 metadata 预筛过滤器（`MCP/Memory/servers/memory_server/server_tools.py`）。检索路径：`metadata facet 预筛 → FTS → vector supplement → rerank`，facet 过滤命中即缩小候选集，未命中不阻塞主链路。

### 17.3 显式不做的扩张

按 P111 偏好与 §1 "无 UE 编辑器依赖" 边界，以下能力**不主动引入**，仅在 record 已显式声明 facet 时被动消费：

- Map / Level / World Partition 引用关系自动抽取（需要 UE Editor 解析 `.umap`，违反 "无 UE 编辑器依赖"）
- Blueprint 节点级依赖图（同上）
- Asset Registry 扫表自动入库（同上 + 体量与索引压力）
- 模块 → 资产系统区映射的人工本体（推荐由 record 层在 `module_names` + `asset_paths` 同记录共现表达，由检索打分自然聚合）

如未来需要任一项，应作为独立 UE Editor MCP plugin 提供 facet 喂入端，本插件继续只持有 facet 字段与索引，保持 runtime 纯本地、无引擎依赖。

