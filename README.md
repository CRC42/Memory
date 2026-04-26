# Memory MCP Server

给 AI 用的「项目记忆服务器」。本仓库下的 MCP 插件，把项目动态记忆（活跃上下文、任务进度、规则、证据、谱系）固化为 Markdown 真源 + SQLite 派生索引，AI 通过 4 个 facade 工具读写、搜索、编译、按预算输出重要记忆，并在显式开关下走 LLM 增强能力。

详细设计见 [MemorySystemDesignDocument.md](./MemorySystemDesignDocument.md)，开发流水见 [DEVLOG.md](./DEVLOG.md)。

---

## 1. 简要介绍

- **定位**：AI 会话的「持久外脑」。把每次会话不该靠聊天历史猜的项目状态，落到磁盘。
- **真源**：`memory-bank/**/*.md`（YAML Front Matter + Markdown 正文）。SQLite FTS、编译结果、缓存全部是可重建的派生视图。
- **三层目录**：
  - `memory-bank/` — 长期项目记忆（一般入 Git）
  - `.ai-context/` — 当前任务热上下文（一般不入 Git）
  - `.ai-memory/` — 配置、索引、备份、审计、缓存（不入 Git）
- **当前能力**：读/搜/写普通文件、结构化记录（candidate → validated → published → archived）、SQLite FTS + 中文 bigram/trigram、deterministic 编译（runtime / snapshot / review / dao-fa-shu 视图）、谱系与冲突追踪、按 token/字符/条数预算的 `retrieve_context` /「重要记忆输出」。
- **MCP 默认工具**：4 个 facade（`memory_read` / `memory_write` / `memory_context` / `memory_enhance`），开发期可开 `expose_admin_tools=true` 暴露共 24 个工具（4 facade + 20 legacy/admin）。
- **LLM 接入**（v0.6.1）：OpenAI 兼容客户端（默认 DeepSeek）+ 原始不可改 / 蒸馏可替换原语 + 输入/输出/总成本预算闸门 + LLM/非-LLM 职责矩阵。详见 §3.5、§4.8。
- **LLM pipeline 接入主路径**（v0.7.0 P4）：`memory_write` 支持 `distill=true` 自动落 distilled 蒸馏记录；`memory_context` 支持 `summarize=true` 把召回结果交给 LLM 概括；内置 SHA-256 内容哈希 dedup + token 估算 chunking + map-reduce orchestrator。详见 §3.6、§5.1。
- **LLM 增强接口 facade**（v0.7.0 P4-B）：`memory_enhance` 单一 MCP 工具路由 6 个 opt-in LLM 能力（classify_record / extract_candidates / merge_candidates / generate_skill_candidate / explain_conflict / generate_handoff）。只返回结构化建议，不写盘；上层决定是否以 candidate 謡落 `memory_write_record`。详见 §3.7、§5.1.B。
- **状态**：v0.7.0-P4。全套 475 项测试全过（v0.6.1 416 + LLM pipeline 接入 26 + LLM 增强接口 33）；活环境冲烟已验证 DeepSeek wire-format 可联通。

---

## ⚠️ 团队接入前必读：版本控制 ignore 配置

> **接入团队 Git/SVN 之前必须完成以下 ignore 配置，否则会出现高频不可自动合并冲突。**这是 §4.1「真源 = Markdown，数据库只是索引」的硬约束在 VCS 层面的延伸。

### 必须忽略（漏配 → 必冲突 / 数据损坏）

| 路径 | 原因 |
|------|------|
| `.ai-memory/` | 索引、锁、审计、备份、缓存全部本机派生 |
| `.ai-context/` | 个人热上下文 |
| `memory-bank/compiled/` | deterministic 编译产物，可重建 |
| `memory-bank/.tmp*` | 原子写入临时文件 |
| `.vscode/settings.json` | 含 `memory-mcp.userName` 个人身份 |

### 必须保留入库（团队真源）

| 路径 | 说明 |
|------|------|
| `memory-bank/**/*.md` | 长期项目记忆真源 |
| `.ai-memory/config.json` | 团队共用策略配置（**例外**：`.ai-memory/` 仅这一个文件入库） |
| `.vscode/mcp.json` | 团队共用 MCP 服务器注册 |

### Git — 推荐 `.gitignore` 片段（复制即用）

```gitignore
# === Memory MCP ===
# 派生 / 本机运行时
.ai-memory/
.ai-context/
memory-bank/compiled/
memory-bank/.tmp*
# 例外：团队共用配置入库
!.ai-memory/config.json
# VS Code
.vscode/settings.json
!.vscode/mcp.json
```

### SVN — 等价配置

```bash
svn propset svn:ignore "compiled
.tmp*" memory-bank
svn propset svn:ignore "*" .ai-memory
svn propset svn:ignore "*" .ai-context
svn add .ai-memory/config.json --force
```

> ⚠️ **SVN 团队额外约定**：**不要对 `progress.md` / `techContext.md` / `systemPatterns.md` / `projectbrief.md` 使用 `svn lock`**，否则会卡住其他开发者的 agent 写入。这些文件已被服务端强制为 append-only，不需要 lock。

### 接入前自检清单

- [ ] `.gitignore` / `svn:ignore` 已包含 `.ai-memory/` 与 `.ai-context/`
- [ ] `.ai-memory/config.json` 例外保留入库
- [ ] `memory-bank/compiled/` 在 ignore 列表
- [ ] `.vscode/settings.json` 不入库（每人本地 `bootstrap.ps1` 时生成）
- [ ] 团队约定不对共享 append-only 文件加锁
- [ ] CI 上不会误把 `.ai-memory/search.db` / `events.jsonl` 提交

满足以上 6 条后，**记忆文件在 Git 团队下的自动合并率 ≥ 99%**（仅 `progress.md` 等 append-only 文件偶发尾部冲突，等同普通代码 3-way merge）。详见 §6 多人项目就绪度评估。

---

## 2. 部署方式

下列命令均在仓库根 `C:\Work\GIT\ToolTest` 执行。

### 2.1 创建虚拟环境 + 安装依赖

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1 -InstallDeps
# 开发/测试场景再加：
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1 -InstallDevDeps
```

`deploy.ps1` 优先安装 `MCP/Memory/vendor/` 内的离线 wheel，失败再回落 pip 在线源。

运行依赖见 [requirements.txt](./requirements.txt)（核心：`mcp>=1.0.0`、`pytest>=7,<9`）。中文搜索由插件内置 CJK n-gram 实现，无第三方分词依赖。

### 2.2 注册到 MCP 客户端

VS Code：

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/setup_mcp.ps1
```

会把 `project-memory-mcp` 写入 `.vscode/mcp.json`。

Codex（`%USERPROFILE%\.codex\config.toml`）：

```toml
[mcp_servers.project-memory-mcp]
command = 'C:\Work\GIT\ToolTest\MCP\Memory\.venv\Scripts\python.exe'
args = ['-m', 'servers.memory_server', '--root', 'C:\Work\GIT\ToolTest']
env = { PYTHONPATH = 'C:\Work\GIT\ToolTest\MCP\Memory', PYTHONUTF8 = '1' }
```

修改 MCP 配置后需重启客户端或新开会话。

### 2.3 验证

```powershell
# 启动调试
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_server.ps1

# 跑全部测试（应输出 475 passed）
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_all_tests.ps1
```

测试脚本优先使用 `MCP/Memory/.venv`，缺 `pytest` 时回落到仓库根 `.venv`。

---

## 3. 使用方式

### 3.1 默认 facade（推荐）

| 工具 | 主要用途 |
|---|---|
| `memory_read` | 读文件 / 搜普通记忆 / 搜结构化记录 / 读 runtime digest |
| `memory_write` | 写普通文件 / 写结构化记录 / 写 observation / 关联 artifact / 可选 LLM 蒸馏 |
| `memory_context` | 编译视图 / 追谱系 / 列冲突 / 对比 snapshot / 上下文装配 / 重要记忆输出 / 可选 LLM 召回概括 |
| `memory_enhance` | 6 个 opt-in LLM 能力的 read-only facade（classify / extract / merge / skill / conflict / handoff） |

每个工具通过 `operation` 参数分发到子能力。例：

```jsonc
// 会话开始：拉当前活跃上下文
{ "tool": "memory_read",  "args": { "operation": "get", "path": "memory-bank/activeContext.md" } }

// 会话结束：覆写 activeContext
{ "tool": "memory_write", "args": { "path": "memory-bank/activeContext.md", "content": "...", "mode": "overwrite" } }

// 提取「当前最重要的若干条记忆」，按预算严格裁剪
{ "tool": "memory_context", "args": { "operation": "important_memories", "max_tokens": 1200, "max_items": 5 } }

// 多 agent 并发场景：先读到 sha，写入时通过 if_match 拒绝丢失更新
// 服务端在锁内重读对比；冲突时返回 error="conflict" + current_sha + request_id
{ "tool": "memory_write", "args": {
    "path": "memory-bank/activeContext.md",
    "content": "...",
    "if_match": "<上次读到的 sha256>"
} }
```

返回值含 `request_id`（UUID7，可由调用方传入做幂等重试）+ `new_sha`（可作为下次 `if_match`）。

### 3.2 推荐工作流

1. **会话开始**：`memory_read.get` 拉 `memory-bank/activeContext.md` + `.ai-context/current-task.md`，恢复上下文。
2. **过程中**：必要时 `memory_read.search` / `memory_read.search_records` 检索；产生新洞见用 `memory_write.record` 写 candidate。
3. **会话结束**：`memory_write.file` 覆写 activeContext；如有里程碑/规则/证据，再写到 `progress.md` / `techContext.md` / `systemPatterns.md` 或对应 candidate。

### 3.3 多人协作

多人模式默认启用，不需要额外配置。所有人对 `memory-bank/activeContext.md` 的读写自动重定向到 `memory-bank/activeContext/{user}.md`，避免 Git 冲突。其余共享文件（`progress.md` 等）默认走 append-only 策略。

旧版 `.ai-memory/config.json` 即便没有 `multi_user` 字段，加载时也会合并默认多人策略；如果旧配置覆盖了 `guard.targets` 且没有写 `write_policy`，服务端仍会根据默认 `multi_user.user_scoped_paths` / `shared_paths_policy` 执行分流与 append-only。

用户名优先级：`.vscode/settings.json` 的 `memory-mcp.userName` → 系统 `USERNAME`/`USER` → `unknown`。

### 3.4 开发期：暴露 admin 工具

在 `.ai-memory/config.json`：

```json
{ "mcp": { "expose_admin_tools": true } }
```

打开后总计暴露 24 个工具（4 facade + 20 legacy/admin：`memory_get` / `memory_search` / `memory_search_records` / `memory_guard_check` / `memory_backup` / `memory_compact` / `memory_write_record` / `memory_rebuild_index` / `memory_update_index` / `memory_compile` / `memory_get_runtime_digest` / `memory_validate_candidate` / `memory_publish_candidate` / `memory_archive_record` / `memory_delete_record` / `memory_record_observation` / `memory_link_artifact` / `memory_trace_lineage` / `memory_health_check` / `memory_migrate_records`）。注：legacy `memory_write` 与 facade `memory_write` 同名，已在合并时去重，故 admin 总数为 24（不是 25）。生产/普通用户保持默认 4 facade 即可。

### 3.5 LLM 接入（v0.6.1）

LLM 是 **可选插件式蒸馏器**，不在主路径上。关闭 LLM 时所有写入/检索/编译能力照常工作（详见 §4.8）。

**配置**

```powershell
# 1) 复制模板
Copy-Item MCP/Memory/llm_config.example.json MCP/Memory/llm_config.local.json
# 2) 填 api_key（文件已 gitignore）
# 3) 也支持 env：MEMORY_LLM_API_KEY / MEMORY_LLM_BASE_URL / MEMORY_LLM_MODEL ...
```

默认指向 DeepSeek（`https://api.deepseek.com`，模型 `deepseek-chat`，OpenAI 兼容）。可指向任意 OpenAI-style endpoint。

**成本闸门**（默认值就足以防失控）

| 配置项 | 默认 | 作用 |
|---|---|---|
| `max_output_tokens_per_call` | 1024 | 每次调用的 `max_tokens` 上限，钳制超过的请求 |
| `max_input_tokens_per_call` | 32000 | 估算 prompt token 超限 → `LLMInputTooLarge`，**不触网** |
| `max_total_output_tokens` | 200000 | 进程累计输出 token 预算 → `LLMBudgetExceeded` |
| `input_cny_per_mtok` / `output_cny_per_mtok` | 1.0 / 2.0 | DeepSeek-flash 价格，记账用 |
| `max_total_cost_cny` | 0（关闭） | 进程累计估算成本上限（CNY） |
| `default_thinking` | false | 思考模式默认关；按需 per-call `thinking=True` |

`LLMClient.usage_snapshot()` 输出 `call_count` / `total_prompt_tokens` / `total_completion_tokens` / `total_estimated_cost_cny`。

**核心 API**

```python
from servers.memory_server.memory_llm import (
    LLMClient, make_raw_record, distill_raw_records,
)
client = LLMClient()  # 自动加载 llm_config.local.json / env
raws = [make_raw_record(record_id="r-1", content="...", source="ue_mcp", captured_at="...")]
distilled = distill_raw_records(client, raws, record_id="d-1", distilled_at="...")
# distilled["derived_from"] == ["r-1"]，可被任意 LLM 重做
```

**关键不变量**

- raw 记录一旦 `make_raw_record` → `immutable=True`，任何 LLM 都不能改/删
- distilled 记录 `replaceable=True` + `derived_from=[raw_ids]`，可被任意 LLM 自由覆盖（`supersede_distilled`）
- LLM 输出**永不**直接落入 raw 真源或非 LLM 索引

详见设计文档 §2.0 / §2.1.A / §11.3.1 / §12。

### 3.6 LLM pipeline 接入主路径（v0.7.0-P4）

```jsonc
// memory_write 自动蒸馏（opt-in）
{
  "operation": "record",
  "content_markdown": "Decision: switch logging backend to spdlog because of perf gains.",
  "record_kind": "decision",
  "scope": "personal",
  "distill": true,                        // 默认 false；仅在 true 时调 LLM
  "distill_user_instruction": "...",      // 可选
  "distill_max_tokens": 1024              // 可选
}
// 返回 result.distilled = {ok, summary, distilled_record_id, distilled_path, model, pipeline, usage, persist_result}
// 失败：{ok: false, error: "llm_unavailable"|...}；主写仍然成功，raw 已落盘
```

```jsonc
// memory_context 召回结果 LLM 概括（opt-in）
{
  "operation": "retrieve_context",
  "query": "subsystem A",
  "top_k": 5,
  "summarize": true,
  "summary_query": "subsystem A 设计决策回顾",
  "summary_max_chars_per_record": 4000
}
// 返回 result.summary = {ok, summary, model, pipeline:{chunks,llm_calls,cache_hits,reduced}, usage}
```

实现要点：
- `memory_llm_pipeline.compute_distill_cache_key` 用 `model + system + user + records` 做 SHA-256 ⇒ 同输入同模型不重复花 token
- `chunk_raw_records` 按 token 估算贪心切块，超 budget 自动 map-reduce
- raw 永远先落盘；LLM 失败仅 in-band 错误，绝不影响主写

### 3.7 LLM 增强接口 facade（v0.7.0-P4-B）

`memory_enhance` 是单一 MCP 工具入口，按 `operation` 字段路由 6 个 opt-in LLM 能力。**全部 read-only**：
返回结构化建议，**不写盘**；上层根据需要把结果以 `status="candidate"` 经 `memory_write_record` 落盘。

| operation | 输入要点 | 返回字段 |
|-----------|----------|----------|
| `classify_record` | `content_markdown` + 可选 `allowed_kinds/scopes/tags` | `record_kind`, `scope`, `tags`, `confidence∈[0,1]`, `rationale` |
| `extract_candidates` | `content_markdown` + 可选 `source_record_id` | `candidates:[{kind∈{claim_candidate,rule_candidate}, content_markdown, confidence, tags, rationale, source_record_id}]` |
| `merge_candidates` | `candidates:[{id, content_markdown}]`（≥2） | `groups:[{representative_id, member_ids[], merged_content_markdown, rationale}]`（必须**完整划分**输入 id） |
| `generate_skill_candidate` | `records:[{...}]` | `title`, `content_markdown`, `tags`, `confidence`, `rationale`, `source_record_count` |
| `explain_conflict` | `record_a`, `record_b` | `conflict_type∈{contradiction,overlap,scope_mismatch,no_conflict,unclear}`, `severity∈{low,medium,high}`, `explanation`, `resolution_options[]` |
| `generate_handoff` | `records:[{...}]` + 可选 `task_id`, `branch` | `summary_markdown`, `key_points[]`, `open_questions[]`, `next_actions[]`, `source_record_count` |

通用：所有响应携带 `model` 与 `usage_delta = {prompt_tokens, completion_tokens, estimated_cost_cny}`；失败统一 in-band 返回 `{error:"enhance_failed:<op>"|"llm_unavailable"|"invalid_input", reason}`。

```jsonc
// 示例：分类
{
  "operation": "classify_record",
  "content_markdown": "决定将日志后端切换到 spdlog 以获得更高吞吐...",
  "allowed_kinds": ["decision","note","claim_candidate"],
  "max_tokens": 400
}
```

```jsonc
// 示例：合并候选
{
  "operation": "merge_candidates",
  "candidates": [
    {"id":"a","content_markdown":"使用 spdlog 的原因 1"},
    {"id":"b","content_markdown":"spdlog 性能数据 ..."},
    {"id":"c","content_markdown":"无关：其他主题"}
  ]
}
```

```jsonc
// 示例：生成交接
{
  "operation": "generate_handoff",
  "task_id": "TASK-123",
  "branch": "feature/x",
  "records": [{"content_markdown":"今日完成 ..."}, {"content_markdown":"待办 ..."}]
}
```

实现要点：
- 单文件 `memory_llm_enhance.py`，`_parse_json_response` 容忍 ```` ```json ``` ```` 围栏与杂散文本；解析失败 → `LLMEnhanceError`
- 6 个能力均通过 `memory_llm_policy.LLM_CAPABILITY_MATRIX` 的能力名（`classify` / `extract_claims` / `merge_candidates` / `generate_skill_candidate` / `explain_conflict` / `generate_handoff`）；未注册即 `UnknownCapability`
- 严格 allowlist：`classify_record` 的 `kind/scope` 必须落入入参 allowed list（默认取 `ALLOWED_RECORD_KINDS / ALLOWED_SCOPES`），未通过即 `enhance_failed:classify_record`
- `merge_candidates` 必须输出输入 id 的**全分区**（无遗漏、无重复、无未知 id）

---

## 4. 设计思想

### 4.1 真源 = Markdown，数据库只是索引

`memory-bank/**/*.md` 是唯一真源；`.ai-memory/search.db`、`.ai-memory/compile-cache/`、`memory-bank/compiled/` 都是派生视图，删了能重建（`memory_rebuild_index`、`memory_compile`）。

这样确保：

- Git diff 能看清记忆变更
- 不绑定特定数据库，索引格式可以演进
- 索引坏了不会丢真源

### 4.2 写入安全 > 写入便利

每次写入都走「白名单路径校验 → 跨进程文件锁（`.ai-memory/locks/<sha>.lock`）→ 全局预算检查 → 备份原文件 → 同目录 tmp 文件 + `O_CREAT|O_EXCL` + `fsync`（数据 fd + POSIX parent dir） → `os.replace` 原子替换 → 审计日志（含 `request_id`/`new_sha`）」。状态迁移（candidate → published）会拒绝悄默覆盖既有目标（返回 `target_exists`），并在锁内做 TOCTOU 复检。Markdown 文件追加 `<!-- last overwritten by ... -->` 用户尾注，JSON/YAML/源码等结构化文件默认不注入注释。

**多 agent 单 mcp 规格部署**（多个 VS Code 窗口 / Codex 会话各自启动一个 server 进程指向同一 workspace）下：所有写路径（`memory_write`、`write_record_to_target`、`record_usage_stats`、`tombstones.jsonl` append、events.jsonl）均按目标文件粒度跨进程串行化；SQLite 走 WAL + `busy_timeout=30000ms`；客户端可传 `if_match`（SHA-256 ETag）由服务端在锁内做乐观锁，冲突时返回结构化 `conflict` 而不是默默覆盖。

**崩溃 / 断电耐久度**：默认 best-effort fsync（兑现 v0.5.4 补 v0.5.3 遗留问题之后的收尾，v0.5.5 只补位 parent-dir fsync 与 events.jsonl fsync）。生产环境如需「磁盘一接起错就让调用方看到」，可在 `.ai-memory/config.json` 设 `mcp.fsync_strict=true`：fsync 失败不再被吞，`memory_write` 返回 `error="write_failed"`，原文件零损伤。

### 4.3 Deterministic > 智能

编译、评分、谱系都是确定性算法，不依赖 LLM：相同输入 → 相同输出。`importance = governance + usage + impact + novelty + conflict + decay`，每一项都是可独立测试的纯函数。这让记忆能 reproducibly 重建、能审计、能离线运行。LLM/RAG 在路线图上是 P4/P5 软增强层，不进入主路径。

### 4.4 预算优先 > top_k

新接口 `memory_context.important_memories` 不再以「取前 K 条」为主，而是接受 `max_tokens` / `max_chars` / `max_items`，输出装得下的最重要记忆 + `evidence_refs`（含 `source_refs` / `related_artifact_ids` / 记录 `path` / `id`）+ `dropped_candidates`（含丢弃原因）+ `budget_report`。给上层 agent 一个可控、可解释的「装入对话窗口」决策。

### 4.5 治理是兼容层，不是核心

candidate → validated → published → archived 流程仍然可用（带 confidence、source_refs、owner、reviewer 校验），但插件本身不主动「自动晋升」。主线是「保真存储 + 高效检索 + 重要记忆输出」，把审查决定权留给用户或外部 agent。

### 4.6 多人协作默认开启

`activeContext.md` 自动按 user 分文件、共享文件 append-only、`scope=personal` / `scope=user_private` 在 compile 与 retrieval 双路径都做作者隔离，防止把别人的私有记录误编译进自己的 runtime digest。

### 4.7 Facade 收敛 MCP 表面

默认只暴露 4 个 facade，避免 AI 客户端在 20+ 低频管理工具里迷路。admin/legacy 工具改走 CLI / scripts / skill / 显式开关，以「日常调用集」与「治理/维护集」分层。

### 4.8 LLM 只做 LLM 擅长的，其余全部确定性

> **raw 不可改 → LLM 想接哪都行；但只接 LLM 真正擅长的环节。**

判断要不要在某处用 LLM：

1. 非 LLM 已经做得足够好？→ **不引入**（确定性、零成本、零延迟）。
2. 离开 LLM 就根本做不好？→ **引入**，但落 distilled 层、可被任意 LLM 重建、不污染 raw。

能力归属由 [`memory_llm_policy.py`](servers/memory_server/memory_llm_policy.py) 单一事实源管理：`LLM_CAPABILITY_MATRIX` + `should_use_llm()` + `must_be_deterministic()`。未注册的能力 → `UnknownCapability`，强制每次决策显式登记。

| 锁死为非-LLM | 由 LLM 做 | hybrid（LLM 提议 → 非-LLM 落盘） |
|---|---|---|
| raw 写入 / 哈希 / 冻结 | 自然语言摘要 | 冲突识别（语义层） |
| Front Matter 解析 / Schema 校验 | 跨 raw 主题聚类 | 自然语言查询解析 |
| 事件日志 / Lock / Backup / Compactor | 自由文本重写 |  |
| FTS 检索 / 评分 / 衰减 |  |  |
| Token 估算 / 模板 digest |  |  |
| 链路追溯 / 治理 / 预算控制 |  |  |

**系统级不变量**

- 任意环节关闭 LLM，系统功能不退化为 0：raw 仍写入、FTS 仍可检索、模板 compile 仍出 digest
- LLM 输出永不直接覆盖 raw 或非 LLM 索引
- distilled 可丢可重建，不损坏真源

完整矩阵与集成图见设计文档 §12。

---

## 5. 开发计划与状态

### 5.1 v0.6.1 LLM 接入 + 自治蒸馏 + 职责矩阵（已完成 ✅）

测试 333 → **416 passed**。详见设计文档 §2.0 / §2.1.A / §11.3.1 / §12，DEVLOG 2026-04-25 条目。

| 模块 | 关键能力 |
|------|----------|
| `memory_llm.py` | OpenAI 兼容客户端（默认 DeepSeek），`thinking`/`reasoning_effort` 开关，输入 cap (`LLMInputTooLarge`) + CJK-aware prompt 估算 + 输出 cap + 总成本预算 (`LLMBudgetExceeded`) + `usage_snapshot` 含估算 CNY |
| `memory_llm.py` 原语 | `make_raw_record`（immutable=True 写一次冻结）、`make_distilled_record`（必须 `derived_from`）、`assert_raw_writable`（写守卫）、`supersede_distilled`（任意 LLM 可重建）、`distill_raw_records`（raw → LLM → distilled 桥） |
| `memory_llm_policy.py` | `LLM_CAPABILITY_MATRIX` 单一事实源（18 项能力 → `non_llm`/`llm`/`hybrid`），`should_use_llm` / `must_be_deterministic` / `UnknownCapability` 强制注册 |
| 配置 | `llm_config.example.json`（gitignored `llm_config.local.json`）+ `MEMORY_LLM_*` env 全套覆盖 |

### 5.1.A v0.7.0-P4 LLM pipeline 接入主路径（已完成 ✅）

测试 416 → **442 passed**（+26：20 项 pipeline 单元 + 6 项 dispatch 集成）。

| 模块 / 接口 | 关键能力 |
|------|----------|
| `memory_llm_pipeline.py`（NEW） | `compute_distill_cache_key`（SHA-256 over `model + system + user + records`）、`DistillCache`、`chunk_raw_records`（greedy by token estimate，floor 1024）、`map_reduce_distill`（多 chunk 走 reduce，单 chunk 直出）、`summarize_records_for_recall`（召回结果 LLM 概括） |
| `memory_write` | 新增 opt-in `distill=true` + `distill_user_instruction` / `distill_max_tokens`；主写成功后异步触发 distill，落到 `record_kind=observation` + `scope=user_private` + `derived_from_record_ids=[raw_id]`；LLM 失败 in-band 报 `llm_unavailable`，主写绝不丢失 |
| `memory_context` | 新增 opt-in `summarize=true` + `summary_query` / `summary_max_tokens` / `summary_max_chars_per_record`；返回 `summary={ok,summary,model,pipeline,usage}` |
| 硬约束 | raw 仍 immutable + authoritative；distilled 仍 replaceable；未触发开关 → 0 LLM 调用 0 token；触发后失败仅返回结构化错误 |

### 5.1.B v0.7.0-P4-B LLM 增强接口 facade（已完成 ✅）

测试 442 → **475 passed**（+33：22 项单元 + 6 项 dispatch + 5 项 _parse_json_response 子项）。详见 §3.7。

| 模块 / 接口 | 关键能力 |
|------|----------|
| `memory_llm_enhance.py`（NEW） | `_parse_json_response`（容忍 ```` ```json ``` ```` 围栏，提取首未尾成对括号，错误住 → `LLMEnhanceError`）、6 个能力函数：`classify_record` / `extract_candidates` / `merge_candidates` / `generate_skill_candidate` / `explain_conflict` / `generate_handoff` |
| MCP `memory_enhance` | 单一 facade，`operation` 路由到上述 6 个能力；**read-only**，返回结构化建议 + `model` + `usage_delta`；未调用 LLM 不会调；失败 in-band 返 `enhance_failed:<op>` / `llm_unavailable` / `invalid_input` |
| 硬约束 | 不写盘 / 不改 raw；`merge_candidates` 必须返回输入 id 的完整划分；`classify_record.kind/scope` 必须落入 allowlist；上层选择是否以 `status="candidate"` 调 `memory_write_record` 落盘 |

### 5.2 v0.6.0 OOTB 硬化（已完成 ✅）

11 项全部按 TDD 落地，测试 230 → **333 passed**。详见 [DEVLOG.md](./DEVLOG.md) 2026-04-26 条目与设计文档 §15.9。

| 分类 | 项 | 关键模块 |
|------|-----|---------|
| P0 数据安全 | user-id 强校验 | `memory_users.py` + `memory_write` 前置守卫 |
| P0 数据安全 | 共享文件 overwrite 强制拒绝 | `mcp.shared_overwrite_policy` opt-in |
| P0 数据安全 | 启动 auto-maintenance | `memory_auto_maintenance.run_if_due` |
| P1 OOTB | `bootstrap.ps1` 单一入口 | venv + settings.json + mcp.json + 健康绿灯 |
| P1 OOTB | UE facet 自动推断 + 未知组件 warning | 解析 `*.uproject` / `Build.cs` / `*.uplugin` |
| P1 OOTB | shared 文件按周自动归档 | `memory-bank/archive/<stem>-YYYYWW.md` |
| P1 OOTB | `config_diagnose` | 显示每字段 value + source (`default`/`file`/`env`/`vscode`) |
| P1 OOTB | `link_artifact` 路径归一化 + git_sha | `Content/...` ↔ `/Game/...` |
| P2 健康/演进 | `cli scale-baseline` + 回归告警 | health issue `scale_regression`（默认 2× 因子） |
| P2 健康/演进 | health 启动自愈 | 清理 60s 以上 `*.tmp` / `*.lock` |
| P2 健康/演进 | scoring 策略 hash 一致性 | events.jsonl 写哈希 + health `scoring_strategy_changed` |

### 5.3 v0.7.0 路线（高 → 低）

1. **P4 LLM pipeline 接入主路径**（已完成 ✅）：详见 §5.1.A。
2. **P4-B LLM 增强接口 facade**（已完成 ✅）：`memory_enhance` 单一 facade 路由 6 个 opt-in LLM 能力。详见 §3.7 / §5.1.B。
3. **P5 本地 RAG / 向量补召回**：语义模糊召回与低 FTS 命中场景。向量索引落 `.ai-memory/`，可删可重建，永不作为真源。检索顺序：`metadata → FTS → vector supplement → rerank`。
4. **`bootstrap.ps1` e2e 演练**：在真实开发机（含已有 `.vscode/mcp.json` 和遗留 `settings.json`）上端到端演练并把指纹收回 README/DEVLOG。

> 历史里程碑（v0.4 → v0.5.10）见 [DEVLOG.md](./DEVLOG.md)。

---

## 6. 大型 UE 多人项目就绪度评估（v0.6.1）

以「2-20 人 / 同 workspace / 多 VS Code + Codex 会话」为目标场景。

### 6.1 已就绪能力

| 维度 | 现状 | 关键证据 |
|------|------|---------|
| **OOTB 部署** | ✅ 单脚本完成 | `scripts/bootstrap.ps1` → venv + settings.json + mcp.json + health 绿灯 |
| **用户身份** | ✅ 强校验 + 占位拒绝 | `memory_users.is_placeholder_user`；`mcp.allow_unknown_user=true` 显式覆盖 |
| **多人写入隔离** | ✅ 默认开启 | `activeContext.md` 自动按 user 分文件；`scope=personal/user_private` author 隔离 |
| **共享文件并发** | ✅ append-only + overwrite 拒绝 | `progress.md` 等强制 append；`shared_overwrite_forbidden` 结构化错误 |
| **多 agent 并发** | ✅ 已严格压测 | sidecar 文件锁 + SQLite WAL + UUID7 request_id + If-Match 乐观锁；8 进程 × 60 真追加压测 |
| **崩溃耐久度** | ✅ 可选严格 fsync | `mcp.fsync_strict=true` → 数据 fd + parent dir + events.jsonl |
| **真源可审计** | ✅ Markdown + Git diff | 索引/缓存全部派生可重建（`memory_rebuild_index` / `memory_compile`） |
| **UE 资产关联** | ✅ 路径归一化 + git_sha | `link_artifact`：`Content/Foo.uasset` ↔ `/Game/Foo`，自动附 git short SHA |
| **UE 模块/插件感知** | ✅ 自动推断 + 警告 | `.ai-memory/ue_facets.json`；记录里 `module_names`/`plugin_names` 不在白名单 → `ue_unknown_components` warning |
| **运维自愈** | ✅ 启动自动跑 | `auto_maintenance` 按时间 / 索引陈旧度 / 事件量阈值；health 自动清理孤儿锁 |
| **健康可观测** | ✅ 多维度 | `memory_health_check` 含 `scale_regression` / `strategy_drift` / `self_heal` 子项 |
| **配置可解释** | ✅ `config_diagnose` | 每字段定位到 default/file/env/vscode 的来源 |

### 6.2 适用边界（已设计但需团队规约）

- **「同一文件多人主动 overwrite」** 默认会被拒绝；团队约定哪些文件由谁负责（`activeContext` 自动分人，`progress.md` append，规则类文件单一 owner）。
- **`if_match` ETag** 是可选的；写客户端建议总传，避免覆盖竞态。
- **`bootstrap.ps1`** 在真实开发机的 e2e 演练尚未跑完整一遍（v0.7.0 计划）；目前由 8 项 pytest 覆盖核心 JSON 操作。
- **跨机协作** 仍以 Git 为载体（memory-bank/** 入库；`.ai-memory/` 不入库）。这是设计意图，不是缺口。

### 6.3 v0.7.0 之前 *不能* 假设的能力

- 向量召回（仅 FTS + bigram/trigram；中文长 token 短查询的召回兜底依赖人工 tag；P5 路线）。
- 跨机器实时协作（不是 server，没有 WebSocket / 中心服务）。

> v0.7.0-P4 起 LLM 蒸馏自动写盘 / 召回摘要已接入 `memory_write` / `memory_context`，需通过显式 `distill=true` / `summarize=true` 开关启用。v0.7.0-P4-B 另增单一 facade `memory_enhance`（6 个 opt-in LLM 能力），**read-only**，返回结构化建议不写盘；上层可以 candidate 状态调 `memory_write_record` 落盘。

### 6.4 结论

**当前 v0.6.1 已具备「同 workspace 多人 + 多 agent」UE 大型项目的生产可用基线**：

- 数据安全（用户身份、写入冲突、崩溃耐久度）
- 并发安全（跨进程锁、SQLite WAL、乐观锁、压测）
- UE 项目语义（uproject/Build.cs/uplugin 推断、Content↔/Game 归一化、git_sha）
- 运维（OOTB bootstrap、auto-maintenance、self-heal、scale baseline、strategy drift）
- 可观测（health_check 多维 issue、config_diagnose、events.jsonl 审计）

建议落地步骤：
1. **接入 Git/SVN 前先按本 README 顶部 ⚠️「版本控制 ignore 配置」章节配齐 ignore，否则会出现 search.db / events.jsonl / locks 高频冲突**。
2. 每位开发者跑一次 `scripts/bootstrap.ps1`，输入个人 `userName`。
3. 团队约定 `progress.md` / `techContext.md` / `systemPatterns.md` 的 owner，避免 overwrite 竞争（虽然服务端会拒绝，但维护体验更顺）。
4. CI 周期跑 `cli scale-baseline` 写基线、跑 `memory_health_check` 报告异常。
5. 真实开发机首装跟踪 v0.7.0 的 e2e 演练任务，回收边角问题。
