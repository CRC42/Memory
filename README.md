# Memory MCP Server

> 给 AI agent 用的「项目记忆服务器」。Markdown 当真源，SQLite 当索引，4 个 facade 工具收口读写、检索、编译、关键文档自动重建。

详细设计见 [MemorySystemDesignDocument.md](./MemorySystemDesignDocument.md)，开发流水见 [DEVLOG.md](./DEVLOG.md)。

---

## 1. 项目说明

- **定位**：AI 会话的持久外脑。把不该靠聊天历史猜的项目状态，落到磁盘，AI 跨会话可读、可写、可检索、可重建。
- **真源**：`memory-bank/**/*.md`（YAML Front Matter + Markdown 正文）。SQLite FTS、编译产物、缓存全部是可重建的派生视图，删了能从 raw 重建。
- **三层目录**
  - `memory-bank/` — 长期项目记忆（入 Git）
  - `.ai-context/` — 当前任务热上下文（不入 Git）
  - `.ai-memory/` — 配置、索引、备份、审计、缓存（不入 Git，例外：`.ai-memory/config.json` 入 Git）
- **MCP 工具表面**：默认 4 个 facade（`memory_read` / `memory_write` / `memory_context` / `memory_enhance`），开发期可开 `expose_admin_tools=true` 暴露 24 个工具（4 facade + 20 admin/legacy）。
- **当前状态**：v0.7.1-P4C-slice2，全套 **506 测试通过**。

---

## 2. 安装方式

下列命令在仓库根 `C:\Work\GIT\ToolTest` 执行。

### 2.1 创建虚拟环境 + 装依赖

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1 -InstallDeps
# 开发/测试环境另外执行：
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1 -InstallDevDeps
```

`deploy.ps1` 优先用 `MCP/Memory/vendor/` 内的离线 wheel，失败再回落 pip 在线源。

依赖：核心 `mcp>=1.0.0`、测试 `pytest>=7,<9`，详见 [requirements.txt](./requirements.txt)。中文搜索由插件内置 CJK n-gram 实现，无第三方分词依赖。

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
# 启动调试模式
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_server.ps1

# 跑全部测试，应输出 506 passed
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_all_tests.ps1
```

### 2.4 团队接入前必做的 Git/SVN ignore

漏配会出现高频不可自动合并冲突。

**必须忽略**：`.ai-memory/`、`.ai-context/`、`memory-bank/compiled/`、`memory-bank/.tmp*`、`.vscode/settings.json`。
**必须保留入库**：`memory-bank/**/*.md`、`.ai-memory/config.json`（`.ai-memory/` 内的唯一例外）、`.vscode/mcp.json`。

`.gitignore` 片段：

```gitignore
# === Memory MCP ===
.ai-memory/
.ai-context/
memory-bank/compiled/
memory-bank/.tmp*
!.ai-memory/config.json
.vscode/settings.json
!.vscode/mcp.json
```

SVN 团队额外约定：**不要对 `progress.md` / `techContext.md` / `systemPatterns.md` / `projectbrief.md` 加 `svn lock`**，服务端已强制 append-only，加锁会卡住其他人。

---

## 3. 使用方式

### 3.1 4 个 facade 工具

| 工具 | 主要用途 |
|---|---|
| `memory_read` | 读文件 / 搜普通记忆 / 搜结构化记录 / 读 runtime digest |
| `memory_write` | 写文件 / 写记录 / 写 observation / 关联 artifact / 可选 LLM 蒸馏 |
| `memory_context` | 编译视图 / 追谱系 / 列冲突 / 上下文装配 / 重要记忆输出 / 关键文档重建 / 可选 LLM 召回概括 |
| `memory_enhance` | 6 个 opt-in LLM 能力的 read-only facade（classify / extract / merge / skill / conflict / handoff） |

每个工具靠 `operation` 字段分发到子能力。返回值统一含 `request_id`（UUID7，幂等重试）+ `new_sha`（可作为下次 `if_match`）。

### 3.2 推荐工作流（无感模式）

> 核心规则：**只往 raw 写事实，不再手动覆写关键文档**。`activeContext.md` / `progress.md` / `techContext.md` / `systemPatterns.md` 是派生视图，由 `rebuild_key_documents` 重建。

1. **会话开始**：`memory_read.get` 拉 `memory-bank/activeContext.md` + `.ai-context/current-task.md`，恢复上下文。
2. **过程中**：
   - 检索：`memory_read.search` / `memory_read.search_records`。
   - 落事实：`memory_write.record` 写 raw（observation / decision / incident / note / claim_candidate / rule_candidate）。**写一次即冻结**。
3. **会话结束**：`memory_context.rebuild_key_documents` 自动重建关键文档。
   - `renderer="auto"`（默认走 `key_documents.renderers.prefer_order = ["llm", "deterministic"]`）：LLM 可用走 LLM，否则自动回落 deterministic。
   - 失败时 raw 完整保留，旧版本派生文档从 backups/pre_rebuild 还原。

派生文档头部强制写入 `<!-- generated_by=memory-mcp renderer=… source_record_ids=[…] generated_at=… config_hash=… -->`，git diff 与审计工具可识别。

**过渡期兼容**：`.ai-memory/config.json` 设 `key_documents.mode="manual"` 退回 v0.6 手写工作流；`mode="disabled"` 完全禁用 rebuild。手写视为快速通道，下次 rebuild 前自动归档到 `memory-bank/archive/manual-edits/`。

### 3.3 多人协作

默认开启，无需额外配置。

- `memory-bank/activeContext.md` 自动按 user 重定向到 `memory-bank/activeContext/{user}.md`，避免 Git 冲突。
- 共享文件（`progress.md` 等）默认 append-only。
- `scope=personal` / `scope=user_private` 在 compile 与 retrieval 双路径都做作者隔离。
- 用户名优先级：`.vscode/settings.json` 的 `memory-mcp.userName` → 系统 `USERNAME`/`USER` → `unknown`。

并发写入：客户端可传 `if_match`（SHA-256 ETag），服务端在锁内做乐观锁，冲突返回 `error="conflict"` + `current_sha` + `request_id`。

### 3.4 开发期暴露 admin 工具

`.ai-memory/config.json`：

```json
{ "mcp": { "expose_admin_tools": true } }
```

总计 24 个工具暴露（4 facade + 20 admin/legacy：`memory_get` / `memory_search` / `memory_search_records` / `memory_guard_check` / `memory_backup` / `memory_compact` / `memory_write_record` / `memory_rebuild_index` / `memory_update_index` / `memory_compile` / `memory_get_runtime_digest` / `memory_validate_candidate` / `memory_publish_candidate` / `memory_archive_record` / `memory_delete_record` / `memory_record_observation` / `memory_link_artifact` / `memory_trace_lineage` / `memory_health_check` / `memory_migrate_records`）。

### 3.5 LLM 接入

LLM 是**可选插件式蒸馏器**，不在主路径上。关闭 LLM 时所有写入 / 检索 / 编译 / deterministic 重建均正常。

```powershell
Copy-Item MCP/Memory/llm_config.example.json MCP/Memory/llm_config.local.json
# 填 api_key（文件已 gitignore）
# 也支持 env：MEMORY_LLM_API_KEY / MEMORY_LLM_BASE_URL / MEMORY_LLM_MODEL ...
```

默认指向 DeepSeek（OpenAI 兼容），可指任意 OpenAI-style endpoint。成本闸门见 [llm_config.example.json](./llm_config.example.json) 与 [DEVLOG.md](./DEVLOG.md) v0.6.1 条目。

LLM 三个接入点：

- `memory_write.record` 加 `distill=true` → 主写成功后落 distilled 蒸馏记录（失败 in-band 报错，主写绝不丢）。
- `memory_context.retrieve_context` 加 `summarize=true` → 召回结果走 LLM 概括。
- `memory_context.rebuild_key_documents` 走 `renderer="auto"|"llm"` → LLM 渲染派生文档（仅填正文，header/title/role 仍由 deterministic 控制）。
- `memory_enhance` → 6 个 read-only LLM 能力（classify_record / extract_candidates / merge_candidates / generate_skill_candidate / explain_conflict / generate_handoff），返回结构化建议**不写盘**，上层选择是否以 candidate 状态落盘。

---

## 4. 项目设计思想

### 4.1 真源 = Markdown，数据库只是索引

`memory-bank/**/*.md` 是唯一真源。`.ai-memory/search.db` / `compile-cache/` / `memory-bank/compiled/` 全部是派生视图，删了能重建。Git diff 看清记忆变更，不绑定特定数据库，索引坏了不丢真源。

### 4.2 写入安全 > 写入便利

每次写入：白名单路径校验 → 跨进程文件锁 → 全局预算检查 → 备份原文件 → 同目录 tmp + `O_CREAT|O_EXCL` + `fsync`（数据 fd + parent dir） → `os.replace` 原子替换 → 审计日志（含 `request_id` / `new_sha`）。

多 agent 单 workspace 下：所有写路径按目标文件粒度跨进程串行化，SQLite WAL + `busy_timeout=30000ms`，`if_match` 乐观锁拒绝丢失更新。生产环境可设 `mcp.fsync_strict=true` 让 fsync 失败硬抛 `write_failed`，原文件零损伤。

### 4.3 Deterministic > 智能

编译、评分、谱系都是确定性算法，不依赖 LLM：相同输入 → 相同输出。`importance = governance + usage + impact + novelty + conflict + decay`，每一项都是可独立测试的纯函数。LLM/RAG 只做软增强层，不进入主路径。

### 4.4 预算优先 > top_k

`memory_context.important_memories` 接受 `max_tokens` / `max_chars` / `max_items`，输出装得下的最重要记忆 + `evidence_refs` + `dropped_candidates`（含丢弃原因）+ `budget_report`。给上层 agent 一个可控、可解释的「装入对话窗口」决策。

### 4.5 关键文档 = 派生视图（v0.7.1 doctrinal pivot）

`activeContext.md` / `progress.md` / `techContext.md` / `systemPatterns.md` 不再是主编辑文件，而是 raw 集合的可重建渲染。

- 用户/AI 只往 raw 写；不手动 overwrite 这些文件（手写仍可，下次 rebuild 时自动归档到 `archive/manual-edits/`）。
- `memory_context.rebuild_key_documents` 是唯一约定的写入入口。
- 三档渲染：**LLM → embedding 模板（待 P5）→ deterministic**。任意档失败自动跌落下一档；全部失败回滚 backup。
- raw 永远 `immutable=True`，派生文档损坏不污染真源；删掉重建即可。

### 4.6 多人协作默认开启

`activeContext.md` 按 user 分文件、共享文件 append-only、`personal` / `user_private` 作者双路径隔离，不会把别人的私有记录编译进自己的 runtime digest。

### 4.7 Facade 收敛 MCP 表面

默认只 4 个 facade，避免 AI 客户端在 20+ 低频管理工具里迷路。admin/legacy 走 CLI / scripts / 显式开关，分「日常」与「治理」两层。

### 4.8 LLM 只做 LLM 擅长的

判断要不要在某处用 LLM：

1. 非 LLM 已经做得足够好？→ **不引入**（确定性、零成本、零延迟）。
2. 离开 LLM 就根本做不好？→ **引入**，但落 distilled 层，可被任意 LLM 重建，**不污染 raw**。

能力归属由 [`memory_llm_policy.py`](servers/memory_server/memory_llm_policy.py) 单一事实源管理。未注册能力 → `UnknownCapability` 强制每次决策显式登记。

| 锁死为非-LLM | 由 LLM 做 | hybrid（LLM 提议 → 非-LLM 落盘） |
|---|---|---|
| raw 写入 / 哈希 / 冻结 | 自然语言摘要 | 关键文档重建 |
| Front Matter 解析 / Schema 校验 | 跨 raw 主题聚类 | 冲突识别 |
| 事件日志 / Lock / Backup | 自由文本重写 | 自然语言查询解析 |
| FTS 检索 / 评分 / 衰减 |  |  |
| 链路追溯 / 治理 / 预算控制 |  |  |

任意环节关闭 LLM，系统不退化为 0：raw 仍写、FTS 仍检索、deterministic 模板仍出 digest 与关键文档。

---

## 5. 开发历史与计划

按版本倒序。完整流水见 [DEVLOG.md](./DEVLOG.md)。

### 已完成

| 版本 | 主题 | 测试 |
|------|------|------|
| **v0.7.1-P4C-slice2** | LLM 档 + 三档降级编排（auto: llm→deterministic）+ `key_documents.mode/renderers.prefer_order` 配置 | 497 → **506** |
| **v0.7.1-P4C-slice1** | deterministic 关键文档重建（`memory_context.rebuild_key_documents`），4 份派生文档 + header 契约 + 手写归档 + pre_rebuild 备份 | 481 → 497 |
| **v0.7.0-P4-B** | `memory_enhance` 单一 facade 路由 6 个 read-only LLM 能力（classify / extract / merge / skill / conflict / handoff） | 442 → 481 |
| **v0.7.0-P4** | LLM pipeline 接入主路径：`memory_write.distill=true` 自动蒸馏、`memory_context.summarize=true` 召回概括、SHA-256 dedup + token chunking + map-reduce | 416 → 442 |
| **v0.6.1** | LLM 接入 + 自治蒸馏 + 职责矩阵：OpenAI 兼容客户端、`make_raw_record` 写一次冻结、`distill_raw_records`、`LLM_CAPABILITY_MATRIX` 单一事实源 | 333 → 416 |
| **v0.6.0** | OOTB 硬化 11 项：user-id 强校验、共享文件 overwrite 拒绝、启动 auto-maintenance、`bootstrap.ps1`、UE facet 自动推断、shared 周归档、`config_diagnose`、`link_artifact` 路径归一化、scale baseline、health 自愈、scoring strategy hash | 230 → 333 |

历史里程碑（v0.4 → v0.5.10）见 [DEVLOG.md](./DEVLOG.md)。

### 计划中

1. **P4-C-slice3 / P5 — embedding 档（RAG）**：
   - 本地向量索引落 `.ai-memory/`（可删可重建，不作真源）。
   - 检索顺序 `metadata → FTS → vector supplement → rerank`。
   - 同步给关键文档 `renderer="embedding"` 复用，补齐三档降级最后一档。
   - 当前 `renderer="embedding"` 返回 `error="not_implemented"`。
2. **`compiled/snapshots/<doc>-<timestamp>.md` 显式回滚**：当前 `backups/pre_rebuild` + atomic write 已构成等价「原内容不丢」保证；待真实回滚事故触发再做独立 snapshots 子目录与时间戳。
3. **`bootstrap.ps1` e2e 演练**：在真实开发机（含已有 `.vscode/mcp.json` 和遗留 `settings.json`）上端到端跑一遍并把指纹收回 README/DEVLOG。
