# MCP 记忆系统设计文档

> 状态：v0.7.1 无感原则收敛设计稿。P0/P1/P2、P3 结构升级、P3 hardening、P4 LLM pipeline、P4-B `memory_enhance` facade 均已落地；**P4-C「关键文档可重建」是本轮最高优先的未落地项**。
>
> 日期：2026-04-27
>
> 配套文档：实现细节与版本流水见 [README.md](./README.md) 与 [DEVLOG.md](./DEVLOG.md)；原始设计稿备份见 `MemorySystemDesignDocument.md.bak`。

## 0. 当前基线（一句话总结）

仓库已具备：4 个 facade tool（`memory_read` / `memory_write` / `memory_context` / `memory_enhance`）+ 24 个 legacy/admin（默认隐藏）+ CLI（`v0.5.10`）+ 三层目录（`memory-bank/` / `.ai-context/` / `.ai-memory/`）+ 路径安全 + 原子写入 + 跨进程文件锁 + SQLite FTS（含 CJK bigram/trigram）+ schema v2 + 时间快照 + lineage + importance scoring + budget-first retrieval + dao/fa/shu 视图 + LLM map-reduce pipeline + 6 项 read-only LLM 增强能力。详细历史见 DEVLOG。

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

## 15. 待实现计划（仅未完成项）

> 已落地阶段（基础版 / 结构化增强 / 治理发布 / P3 升级 / P3 hardening / P4 LLM pipeline / P4-B `memory_enhance` / v0.5.x 内部重构与并发安全 / v0.5.10 CLI / v0.5.11 非 UE 稳健性收尾）的细节见 [DEVLOG.md](./DEVLOG.md)。

### 15.1 v0.6.0 开箱即用稳健性版（**当前进行中**）

**总目标**：使用者唯一显式配置 = 自己的 user id；其余团队约定全部由插件自动兜底。

**P0 — 健壮性 / 数据安全（必须 TDD）**

| 编号 | 项目 | 关键行为 |
|---|---|---|
| P0-1 | user id 强校验 | `is_placeholder_user(name)` 拒绝 `""` / `unknown` / 含路径分隔符；模糊用户名（`Administrator` / `User` / `admin` / `root` / `guest` / `default`）写 `user_ambiguous` warning；返回结构化 `error="user_not_configured"` + `setup_hint` |
| P0-2 | shared overwrite 强制拒绝 | `shared_paths_policy=append_only` + `mode="overwrite"` 立即返回 `error="shared_overwrite_forbidden"` + `suggested_operation="record"` + `target_user_scoped_path`；不写盘不备份 |
| P0-3 | 启动期 auto-maintenance | `run_if_due(config)` 检查 `last_maintenance.json`，超阈值（默认 `min_interval_hours=168` / `events_max_bytes=50MB` / `index_stale_seconds=600` / `shared_append_max_lines=2000`）按需触发 health/rebuild/compact；幂等、失败不阻塞 |

**P1 — 易用性自动化**

- `bootstrap.ps1`：venv → 装依赖 → 询问 user id（幂等）→ 写 `.vscode/mcp.json`+`settings.json` → 跑一次 health
- UE facet 自动推断：检测根目录 `*.uproject` 时扫 `Source/**/*.Build.cs` + `Plugins/*/*.uplugin` → 写 `.ai-memory/ue_facets.json`；未知 module 给 warning 不阻塞
- shared append auto-compact：超阈值自动 fold 旧条目到 `memory-bank/archive/<file>-YYYYWW.md`
- 配置完全可选 + `memory_context.config_diagnose`：报告每条策略来源（默认 / 文件 / 环境变量）
- `link_artifact` 自动归一化：UE `/Game/X` ↔ 物理 `Content/X.uasset` 双向解析 + 附 `git_sha`

**P2 — 稳健性观测**

- `cli scale-baseline`：跑 5k/20k/50k smoke 写 `.ai-memory/baseline.json`；health 对比基线，回归 50% 报 `warning="perf_regression"`
- health 启动自愈：异常时尝试 rebuild-index、清孤儿 `.tmp`、清过期 lock sidecar
- 策略 schema 哈希一致性提示：写 `events.jsonl`，启动期不一致时警告

**纪律**：TDD 强制；不破坏 4 facade；不依赖 LLM/网络；错误结构化（`{ok:false, error, request_id, hint}`）；每项完成更新 DEVLOG + README。

### 15.2 P4-C 关键文档可重建（**本轮最高优先**）

`activeContext.md` / `progress.md` / `techContext.md` / `systemPatterns.md` 能从结构化记录、snapshot、observation 中确定性重建；误操作、合并冲突、跨机同步丢失后可一键恢复。

- 定义"关键文档 manifest"：列出可重建的目标文件及其证据来源（record kinds / scopes / facet 查询）
- 新增 facade：`memory_context(operation="reconstruct_doc", target=...)` 返回重建预览 + diff
- 新增 CLI：`cli reconstruct --target progress.md --apply`，执行前自动备份原文件
- 重建依赖确定性 pipeline，LLM 仅可选用于叙事顺序 / 文本打磨；无 LLM 时必须输出可读结构化版本
- 必须保留 backup，返回 `request_id` / `evidence_refs` / `dropped_candidates`
- TDD：有/无 LLM 两条路径回归；空 corpus / 谱系中断 / 冲突记录均给结构化错误，禁止静默失败

### 15.3 P4 LLM 软增强其它未完成项

- `query rewrite` 的常规入口与召回接入点
- `snapshot narrative` 的常规入口与 weekly/monthly snapshot 自动衔接
- LLM 增强的默认开启 / 超时 / 失败降级策略完整化

P4 通用约束：不直接发布正式系统记忆；不覆盖真源；不替代 deterministic compile；不直接决定 dao 层；无 LLM 时基础链路完整可用。

### 15.4 P5 本地 RAG / 向量补召回（未启动）

- 语义模糊召回 + 长尾别名补召回
- 向量索引仅放 `.ai-memory/`，可删除可重建，永远不是真源
- 检索顺序：metadata → FTS → vector supplement → rerank

### 15.5 P3+ 多人联合项目治理（已降级为兼容方向，不再扩张）

保留现有 validate / publish / archive 链路用于历史数据；治理动作迁移到 CLI / scripts / 管理 skill；不再把"插件内自动审查与规则晋升"当作主产品方向。晋升原则（如未来需要）：`shu` 允许较快沉淀 / `fa` 要求多次复用或多人验证 / `dao` 只能从稳定 fa 上升且必须人工确认。

### 15.6 后续版本占位

| 版本 | 目标 |
|---|---|
| `v0.6.0` | 开箱即用稳健性（**进行中**，详见 §15.1） |
| `v0.7.0` 余项 | P4-C 关键文档可重建（§15.2，**最高优先**）+ query rewrite + snapshot narrative |
| `v0.7.5` | 向量补召回（§15.4） |

## 16. 最终结论

- **形式**：MCP（接口）+ JSON（传输）+ Markdown+Front Matter（存储）+ SQLite FTS（索引）
- **写入**：raw 冻结由代码生成，distilled 可由任意 LLM 重写，谱系由 `derived_from` 绑死
- **编译**：确定性为主，LLM 仅软增强；产物可重建可丢弃
- **治理**：兼容流程保留，新写入默认 raw + distilled 不需人工 validate / publish
- **兜底**：无 LLM 时基础写入、检索、快照、评分、编译完整可用；关键文档至少可走 deterministic 渲染

下一阶段优先级：

1. **P4-C 关键文档可重建**（§15.2）— 完成无感原则的最后一步
2. **v0.6.0 开箱即用稳健性**（§15.1）— P0/P1/P2 项目同步推进
3. **P4 余项**：query rewrite、snapshot narrative
4. **P5 RAG**：作为 metadata/FTS 后的语义补充，最后评估

详细历史与版本流水：见 [DEVLOG.md](./DEVLOG.md)。当前实现说明：见 [README.md](./README.md)。
