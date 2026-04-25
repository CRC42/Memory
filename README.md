# Memory MCP Server

给 AI 用的「项目记忆服务器」。本仓库下的 MCP 插件，把项目动态记忆（活跃上下文、任务进度、规则、证据、谱系）固化为 Markdown 真源 + SQLite 派生索引，AI 通过 3 个 facade 工具读写、搜索、编译、按预算输出重要记忆。

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
- **MCP 默认工具**：3 个 facade（`memory_read` / `memory_write` / `memory_context`），开发期可开 `expose_admin_tools=true` 暴露 23 个 legacy/admin 工具。
- **状态**：v0.5.10，227 项测试全过（含默认多人模式回归、`retrieve_context` budget-first 回归、5 项多进程并发基线 + 9 项多 agent 严格压力 + 3 项锁/磁盘 P0-P2 守卫 + 8 项写入崩溃安全 + 7 项端到端 MCP-协议回归）。已严格压测「单机多 agent + 单 mcp 规格 + 同工作区」并发安全（8 进程 × 60 events.jsonl 真追加、Ctrl+C 中断不泄锁 fd、`disk_full` 结构化错误码等）：跨进程 sidecar 文件锁 + SQLite WAL + 可选乐观锁（If-Match）+ UUID7 请求 ID；可选 fsync 严格模式（`mcp.fsync_strict`）保证崩溃 / 断电后不丢外部可见写入。

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

# 跑全部测试（应输出 227 passed）
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_all_tests.ps1
```

测试脚本优先使用 `MCP/Memory/.venv`，缺 `pytest` 时回落到仓库根 `.venv`。

---

## 3. 使用方式

### 3.1 默认 facade（推荐）

| 工具 | 主要用途 |
|---|---|
| `memory_read` | 读文件 / 搜普通记忆 / 搜结构化记录 / 读 runtime digest |
| `memory_write` | 写普通文件 / 写结构化记录 / 写 observation / 关联 artifact |
| `memory_context` | 编译视图 / 追谱系 / 列冲突 / 对比 snapshot / 上下文装配 / 重要记忆输出 |

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

打开后总计暴露 23 个工具（`memory_get` / `memory_search` / `memory_compile` / `memory_validate_candidate` / `memory_publish_candidate` / `memory_archive_record` / `memory_health_check` / `memory_record_observation` / `memory_link_artifact` / `memory_trace_lineage` 等）。生产/普通用户保持默认 3 facade 即可。

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

默认只暴露 3 个 facade，避免 AI 客户端在 20+ 低频管理工具里迷路。admin/legacy 工具改走 CLI / scripts / skill / 显式开关，以「日常调用集」与「治理/维护集」分层。

---

## 5. 后续开发计划

优先级（高 → 低）：

1. **P4 — LLM 软增强**：query rewrite、tag/facet 推荐、snapshot narrative、conflict explanation、title/abstract 优化。LLM 不能直接发布系统记忆、不能覆盖真源、不能替代 deterministic compile。
2. **P5 — 本地 RAG / 向量补召回**：仅做语义模糊召回与低关键词命中场景的 recall 增强。向量索引落 `.ai-memory/`，可删可重建，永不作为真源。检索顺序仍为 `metadata → FTS → vector supplement → rerank`。

> 历史里程碑（v0.4 多人协作、v0.5.0 P3 schema v2、v0.5.1 record IO 公共层、v0.5.2 author 隔离 + corpus 解耦、v0.5.3 server/compiler/budget 拆分 + 写入加固、v0.5.4 单机多 agent 并发安全、v0.5.5 崩溃耐久度 + fsync 严格模式、v0.5.6 多 agent 严格压测 + events.jsonl 锁修复 + fd 泄漏修复、v0.5.7 默认启用多人安全策略、v0.5.8 retrieve_context budget-first + compiler render/targets/scoring 拆分、v0.5.9 compiler writer/views 二轮拆分 + memory-admin / memory-snapshot-review skill 首版、v0.5.10 管理 CLI 入口）见 [DEVLOG.md](./DEVLOG.md)。
