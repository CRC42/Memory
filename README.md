# Memory MCP Server

这是一个给 AI 用的“项目记忆服务器”。

它解决的事情很简单：AI 每次开新会话时，不应该只靠聊天历史猜项目状态。这个 MCP 会把项目里的重要记忆保存成文件，支持读取、搜索、写入、备份、多人协作、结构化记录、编译摘要和治理发布。

一句话理解：

> `memory-bank/` 里放长期项目记忆，`.ai-context/` 里放当前任务热上下文，`.ai-memory/` 里放配置、索引、备份和运行数据。

当前状态：

- MCP 工具数：18 个
- 测试数：113 个
- 支持中文搜索：是
- 支持多人协作：是
- 支持离线/预下载依赖：优先使用 `vendor/` 里的 wheel
- P0/P1/P2：已完成
- v0.4.1 P0 修复轮：已完成
- P3 LLM 增强：暂不做

详细设计记录见 [MemorySystemDesignDocument.md](./MemorySystemDesignDocument.md)，开发日志见 [DEVLOG.md](./DEVLOG.md)。

## 它能做什么

最基础的能力：

- 读记忆：AI 可以读取 `memory-bank/activeContext.md`、`.ai-context/current-task.md` 等文件。
- 搜记忆：支持普通关键词，也支持中文短词搜索。
- 写记忆：AI 可以安全写入指定目录，写之前会备份。
- 查容量：防止记忆文件越写越大，把上下文撑爆。
- 多人协作：每个人有自己的 active context，不互相覆盖。

进一步的能力：

- 结构化记录：把一条记忆写成 `Markdown + Front Matter`。
- SQLite FTS 搜索：把记录建索引，搜索更快。
- 记忆编译：把零散记录编译成 runtime digest、handoff、system digest。
- 治理流程：candidate -> validated -> published -> archived。
- 维护工具：健康检查、schema 迁移、增量索引、归档删除 tombstone。

## 快速部署

以下命令都从项目根目录 `C:\Work\GIT\ToolTest` 执行。

### 1. 创建虚拟环境

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1
```

### 2. 安装运行依赖

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1 -InstallDeps
```

`deploy.ps1` 会优先从 `MCP/Memory/vendor/` 安装预下载 wheel。离线安装失败时，才会回退到在线 pip 安装。

当前运行依赖在 [requirements.txt](./requirements.txt)：

```text
mcp>=1.0.0
pytest>=7,<9
```

说明：中文分词没有引入额外 Python 库，当前是项目内置的 CJK bigram/trigram 方案。

### 3. 安装开发/测试依赖

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1 -InstallDevDeps
```

### 4. 注册到 VS Code MCP

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/setup_mcp.ps1
```

它会写入 `.vscode/mcp.json`，注册名是 `project-memory-mcp`。

生成的配置大致长这样：

```json
{
  "servers": {
    "project-memory-mcp": {
      "command": "C:/Work/GIT/ToolTest/MCP/Memory/.venv/Scripts/python.exe",
      "args": ["-m", "servers.memory_server", "--root", "C:/Work/GIT/ToolTest"],
      "env": {
        "PYTHONPATH": "C:/Work/GIT/ToolTest/MCP/Memory",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

### 5. 注册到 Codex

Codex 使用 `C:\Users\<你>\.codex\config.toml`。示例：

```toml
[mcp_servers.project-memory-mcp]
command = 'C:\Work\GIT\ToolTest\MCP\Memory\.venv\Scripts\python.exe'
args = ['-m', 'servers.memory_server', '--root', 'C:\Work\GIT\ToolTest']
env = { PYTHONPATH = 'C:\Work\GIT\ToolTest\MCP\Memory', PYTHONUTF8 = '1' }
```

修改 MCP 配置后，通常需要重启客户端或新开会话，客户端才会重新发现工具。

## 如何运行和测试

### 直接启动服务器

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_server.ps1
```

### 跑全部测试

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_all_tests.ps1
```

当前结果：

```text
113 passed
```

测试脚本会自动找一个带 `pytest` 的 Python：

1. 优先用 `MCP/Memory/.venv`
2. 如果这个环境没有 `pytest`，回退到仓库根目录 `.venv`

也可以手动跑：

```powershell
cd MCP/Memory
..\..\.venv\Scripts\python.exe -m pytest tests/memory_server -q
```

### 单独跑某类测试

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_get_tests.ps1
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_search_tests.ps1
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_guard_tests.ps1
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_backup_tests.ps1
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_compact_tests.ps1
```

## 常用工具

| 工具 | 说明 |
|------|------|
| `memory_get` | 读取记忆文件 |
| `memory_search` | 搜索普通记忆文件，支持中文 |
| `memory_guard_check` | 检查记忆文件是否超出容量 |
| `memory_backup` | 备份指定记忆文件 |
| `memory_compact` | 压缩过长记忆文件 |
| `memory_write` | 安全写入普通记忆文件 |
| `memory_write_record` | 写入结构化记忆记录 |
| `memory_rebuild_index` | 重建 SQLite FTS 记录索引 |
| `memory_search_records` | 搜索结构化记录 |
| `memory_compile` | 编译 runtime digest / handoff / system digest |
| `memory_get_runtime_digest` | 读取已编译 runtime digest |
| `memory_validate_candidate` | 验证候选记录 |
| `memory_publish_candidate` | 发布已验证记录 |
| `memory_archive_record` | 归档记录 |
| `memory_update_index` | 增量更新记录索引 |
| `memory_health_check` | 健康检查 |
| `memory_migrate_records` | 迁移记录 schema |
| `memory_delete_record` | 删除 archived 记录并写 tombstone |

## 文件放在哪里

### `memory-bank/`

长期记忆，通常可以进 Git。

常见文件：

| 路径 | 用途 |
|------|------|
| `memory-bank/activeContext.md` | 当前活跃上下文。多人模式下会自动指向个人文件 |
| `memory-bank/progress.md` | 项目进度 |
| `memory-bank/techContext.md` | 技术背景 |
| `memory-bank/systemPatterns.md` | 架构和约定 |
| `memory-bank/projectbrief.md` | 项目目标 |
| `memory-bank/candidates/` | 候选记录 |
| `memory-bank/shared/` | 已发布共享记录 |
| `memory-bank/people/{user}/` | 个人记录 |
| `memory-bank/archive/` | 归档记录 |
| `memory-bank/compiled/` | 编译出来的摘要，不是真源 |

### `.ai-context/`

当前任务热上下文，一般不进 Git。

| 路径 | 用途 |
|------|------|
| `.ai-context/current-task.md` | 当前任务、目标、进度、下一步 |
| `.ai-context/latest-error.md` | 最新错误摘要 |

### `.ai-memory/`

运行时数据和配置。

| 路径 | 用途 |
|------|------|
| `.ai-memory/config.json` | 配置 |
| `.ai-memory/events.jsonl` | 审计日志（超过 5 MB 自动轮转） |
| `.ai-memory/events.jsonl.YYYYMMDDTHHMMSS` | 审计日志归档，默认保留最近 5 份 |
| `.ai-memory/search.db` | SQLite FTS 索引，可重建 |
| `.ai-memory/backups/` | 写入前备份 |
| `.ai-memory/temp/` | 原子写入临时文件 |
| `.ai-memory/compile-cache/` | 编译 manifest |
| `.ai-memory/usage-stats.json` | 编译时记录使用统计（`record_id → last_used_at`），不写回源 |
| `.ai-memory/tombstones.jsonl` | 删除记录 |

> 审计日志轮转阈值与归档份数可通过环境变量 `MEMORY_MCP_EVENTS_MAX_BYTES` 与 `MEMORY_MCP_EVENTS_MAX_ARCHIVES` 调整。

## 项目结构

```text
MCP/Memory/
├── servers/memory_server/
│   ├── server.py               # MCP 入口、工具定义、dispatch
│   ├── memory_config.py        # 配置加载
│   ├── memory_paths.py         # 路径安全、多人重定向
│   ├── memory_reader.py        # memory_get
│   ├── memory_search.py        # 普通文件搜索
│   ├── memory_writer.py        # memory_write
│   ├── memory_backup.py        # 备份和轮转
│   ├── memory_guard.py         # 容量检查
│   ├── memory_compactor.py     # 压缩
│   ├── memory_records.py       # 结构化记录
│   ├── memory_record_index.py  # SQLite FTS 和中文 n-gram
│   ├── memory_compiler.py      # 记忆编译
│   ├── memory_governance.py    # 验证、发布、归档
│   ├── memory_maintenance.py   # 健康检查、迁移、删除
│   └── memory_events.py        # 审计日志
├── tests/memory_server/        # 113 个测试
├── scripts/                    # 启动和测试脚本
├── vendor/                     # 预下载依赖
├── deploy.ps1
├── setup_mcp.ps1
├── requirements.txt
├── DEVLOG.md
└── MemorySystemDesignDocument.md
```

## 当前测试覆盖

重点覆盖：

- 路径安全
- 普通读写
- 搜索和中文搜索
- guard 容量检查
- 备份和轮转
- 压缩
- 多人读写
- 结构化记录写入
- SQLite FTS 查询计划
- FTS5 保留字符查询，如 `texture-size`、`OR fallback`、引号短语
- 记忆编译
- 编译使用统计写入 `.ai-memory/usage-stats.json`，不回写源记录
- 治理发布
- 记录写入和治理迁移的原子性
- 健康检查和迁移
- 增量索引
- archived-only 删除
- MCP dispatch
- 错误参数健壮性
- 非 Markdown 写入默认不注入 HTML 用户尾注，避免破坏 JSON / YAML / 源码
- 多人迁移 banner，提示旧共享 `activeContext.md` 的归属未经验证
- 审计日志轮转

验证命令：

```powershell
powershell -ExecutionPolicy Bypass -File MCP/Memory/scripts/run_memory_all_tests.ps1
```

## 版本摘要

| 版本 | 说明 |
|------|------|
| v0.4.0 | 多人协作：activeContext 用户分区、append_only、自动迁移 |
| vNext P0/P1/P2 | 结构化记录、FTS 搜索、中文 n-gram、编译、治理、维护、健壮性测试 |
| v0.4.1 | P0 修复轮：FTS5 查询转义、源记录不被编译回写、原子写入/迁移、尾注注入策略、迁移归属提示、审计日志轮转、默认 tag 单源 |
| P3 | LLM 辅助分类、合并、冲突解释等，暂不实现 |
| P4 | 本地 RAG / 向量召回，排在 LLM 增强之后，仅作为语义召回增强，暂不实现 |

## 后续开发计划

当前主干仍然是：

```text
Markdown + Front Matter 真源
  -> SQLite metadata / FTS / CJK n-gram 派生索引
  -> deterministic compile
  -> governance
```

后续增强按以下优先级推进：

1. **P3：LLM 增强**
   - LLM 只用于辅助分类、tag 推荐、候选生成、冲突解释、摘要提炼。
   - LLM 可辅助 query rewrite，把模糊中文问题改写为多组中英关键词和 metadata hint，再交给当前 FTS 检索。
   - LLM 不能直接发布系统记忆，不能跳过 `candidate -> validated -> published`。
   - 无 LLM 时，基础写入、检索、编译和治理必须继续可用。

2. **P4：本地 RAG / 向量召回**
   - 排在 LLM 增强之后，不作为当前优先项。
   - 只解决语义模糊召回，例如“上次那个导出方向问题”找不到精确关键词。
   - 向量索引必须是 `.ai-memory/` 下的派生产物，可删除重建，不能成为真源。
   - 检索顺序应保持：metadata 过滤 -> FTS 召回 -> 向量补召回 -> 规则/LLM 重排。

## 核心逻辑

### 1. 普通文件记忆

这是最早、最直观的一层。

AI 通过 MCP 读取和写入 Markdown 文件，例如：

- `.ai-context/current-task.md`
- `memory-bank/activeContext.md`
- `memory-bank/progress.md`

写入不是直接裸写。`memory_write` 会做这些事：

1. 检查路径是否在白名单里。
2. 检查全局记忆预算是否超限。
3. 写入前备份原文件。
4. 用临时文件加 `os.replace` 原子替换。
5. 记录审计日志。
6. 仅对 `.md` / `.markdown` 文件追加 `<!-- last overwritten by ... -->` 用户尾注；写入 `.json` / `.yaml` / 源码等结构化文件默认不会注入注释，避免破坏文件。如需强制开启 / 关闭，可显式传 `inject_user_tag=true|false`。

这样做的目的不是复杂，而是避免 AI 把项目记忆写坏。

### 2. 多人协作

多人同时用 AI 时，最容易冲突的是 `activeContext.md`，因为每个人都会频繁覆盖它。

所以当前策略是：

```text
memory-bank/activeContext.md
```

在多人模式下自动重定向为：

```text
memory-bank/activeContext/{user}.md
```

也就是说，调用方还是读写 `activeContext.md`，但底层会按用户分开存。

共享文件例如 `progress.md`、`techContext.md` 则强制 append，减少 Git 冲突。

用户名来源优先级：

1. `.vscode/settings.json` 里的 `memory-mcp.userName`
2. Windows `USERNAME` 或 POSIX `USER`
3. `unknown`

### 3. 结构化记录

普通 Markdown 适合人看，但不够适合检索和治理。

所以新增了记录级记忆。每条记录都是：

```markdown
---
id: mem_...
record_kind: rule_candidate
scope: personal
status: candidate
author: tiany
tags:
  - mcp
confidence: 0.82
source_refs:
  - evt_1023
---

# 这是一条记忆

正文内容。
```

好处：

- 人可以直接读 Markdown。
- 机器可以读 Front Matter。
- Git diff 也清楚。
- 不依赖数据库做唯一真源。

数据库只是索引，删了可以重建。

### 4. 中文搜索

当前没有引入中文分词库。

做法是：索引时把中文文本切成 bigram/trigram。比如“尺寸约束”会生成类似：

```text
尺寸 寸约 约束 尺寸约 寸约束
```

这样 SQLite FTS 就能搜中文短词。

这个方案不如专业分词库聪明，但它有几个优点：

- 零额外依赖
- 可离线
- 行为稳定
- 测试好覆盖

### 5. 记忆编译

记录越来越多后，AI 不应该每次读全部文件。

`memory_compile` 会按规则筛选记录，然后生成一个更适合当前任务使用的摘要。

当前支持：

| target | 用途 |
|--------|------|
| `runtime_digest` | 当前会话用的运行时摘要 |
| `task_handoff` | 任务交接摘要 |
| `system_digest` | 系统级记忆摘要 |
| `publish_queue` | 待发布候选列表 |

编译是确定性的，不依赖 LLM。也就是说，同样输入会得到同样输出。

v0.4.1 起，编译不会再把 `last_used_at` 回写到源记录 Front Matter，避免污染 Git diff。使用统计写入 `.ai-memory/usage-stats.json`，需要读取时走 `get_record_last_used_at(config, record_id)`。

### 6. 治理流程

不是所有记忆都应该立刻变成“系统规则”。

当前流程是：

```text
raw/candidate -> validated -> published -> archived
```

含义：

- `candidate`：AI 或人提出的候选记忆。
- `validated`：有人验证过，可信度更高。
- `published`：正式发布为共享记忆。
- `archived`：过期或不再使用。

发布时会检查：

- 是否已经验证
- 是否有验证人
- 是否满足最低 confidence
- 是否需要 source refs
- 发布人是否在 owner 列表
- 是否和已有系统规则冲突

简单说：AI 可以提建议，但不能绕过治理直接把候选变成系统记忆。

## 设计思路

### 设计目标

这个系统不是为了做一个“聪明数据库”。

它的目标是：

- 可靠：写入不能轻易损坏记忆。
- 可读：人打开文件就能看懂。
- 可追踪：谁写了什么要能查。
- 可协作：多人使用时尽量不产生 Git 冲突。
- 可恢复：索引、编译结果都可以重建。
- 可测试：核心行为必须有测试固定。

### 为什么用 Markdown

因为项目记忆既要给 AI 用，也要给人看。

Markdown 的好处：

- Git 友好
- diff 清楚
- 容易手动修
- 不绑定某个数据库

### 为什么数据库只是索引

`.ai-memory/search.db` 是为了搜索快，不是真源。

真源仍然是 `memory-bank/**/*.md`。

这样如果索引坏了，可以直接运行：

```powershell
memory_rebuild_index
```

或者在代码里调用 `memory_rebuild_index(config)` 重建。

### 为什么编译结果不是真源

`memory-bank/compiled/` 里的文件是视图。

它们方便 AI 快速读取，但不应该手动当成唯一事实来源。真正可信的是原始记录和已发布记录。

### 为什么要有 guard

AI 上下文是有容量限制的。记忆文件无限增长后，读起来会慢，也容易把关键内容挤出去。

所以 guard 会检查：

- 每个文件是否太大
- 所有记忆合起来是否太大
- 哪些文件需要压缩或拆分
