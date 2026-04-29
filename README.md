# Memory MCP Server

给 AI agent 用的「项目记忆服务器」。Markdown 当真源，SQLite 当索引，4 个 facade 工具收口读写、检索、编译、关键文档重建。

设计文档见 [MemorySystemDesignDocument.md](./MemorySystemDesignDocument.md)，流水见 [DEVLOG.md](./DEVLOG.md)。

---

## 1. 项目说明

- **定位**：AI 会话持久外脑，跨会话可读、可写、可检索、可重建。
- **真源**：`memory-bank/**/*.md`（YAML Front Matter + Markdown）。SQLite FTS / 编译产物 / 缓存全部是可重建的派生视图。
- **三层目录**
  - `memory-bank/` — 长期项目记忆（入 Git）
  - `.ai-context/` — 当前任务热上下文（不入 Git）
  - `.ai-memory/` — 配置 / 索引 / 备份 / 审计 / 缓存（不入 Git，**例外**：`.ai-memory/config.json` 入 Git）
- **MCP 表面**：默认 4 个 facade（`memory_read` / `memory_write` / `memory_context` / `memory_enhance`），开发期可开 `expose_admin_tools=true` 暴露 24 个工具。
- **同时支持 MCP server 模式与 CLI 模式**（CLI 见 §2.3），两种模式共用同一份代码与数据。
- **测试**：506 passed。

---

## 2. 安装方式

> 约定：`<MemoryRoot>` = 本插件目录（含本 README），`<RepoRoot>` = 目标项目根目录。插件可放在 `<RepoRoot>` 下任意相对路径（`MCP/Memory/`、`Tools/Memory/`、`vendor/memory-mcp/` …），脚本/测试通过 `$PSScriptRoot` 与 `__file__` 自适应。
>
> `<RepoRoot>` 解析顺序：`-RepoRoot` 参数 → `$env:MEMORY_REPO_ROOT` → 标记文件向上查找（`.git` / `.svn` / `.hg` / `pyproject.toml` / `*.uproject` / `*.code-workspace` / `*.sln`）→ `<MemoryRoot>/../..` 兜底 + warning。

### 2.1 一键 bootstrap（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File <MemoryRoot>/scripts/bootstrap.ps1
# 自动检测失败时显式给：
#   ... -RepoRoot <RepoRoot>
```

一站式：建 venv → 装依赖 → 提示稳定 user id → 写 `<RepoRoot>/.vscode/settings.json` 的 `memory-mcp.userName` → 把 `project-memory-mcp` 合并进 `<RepoRoot>/.vscode/mcp.json`（用 `${workspaceFolder}` 占位，跨机直接复用） → 跑 health 绿灯。

依赖：核心 `mcp>=1.0.0`、测试 `pytest>=7,<9`，详见 [requirements.txt](./requirements.txt)。优先用 `<MemoryRoot>/vendor/` 离线 wheel，失败回落 pip 在线。

### 2.1.1 仅部署 venv + 依赖（`deploy.bat` / `deploy.ps1`）

只想把 Python venv 和依赖拉起来（不改 VS Code 配置）就用这一步。**默认行为已经是「建 venv → 装依赖 → 校验 import」一条龙**，无需额外开关。

```bat
:: Windows 推荐：bat 包装，自动 -ExecutionPolicy Bypass，规避机器策略问题
cd <MemoryRoot>
deploy.bat                                     :: 一键安装 + 校验（推荐）
deploy.bat -ForceRecreate                      :: venv 异常时彻底重建
deploy.bat -PythonExe "C:\Py311\python.exe"    :: 指定解释器
deploy.bat -InstallDevDeps                     :: 同时装 pytest 等开发依赖
deploy.bat -RegisterVSCode                     :: 顺便把 mcp.json 写到 <RepoRoot>/.vscode/
deploy.bat -SkipInstall                        :: 仅建 venv，不装依赖
deploy.bat -NoVerify                           :: 跳过 import 校验
```

```powershell
# 也可以直接调 PowerShell 版本（参数完全一致）
powershell -ExecutionPolicy Bypass -File <MemoryRoot>/deploy.ps1
```

Python 解释器自动发现顺序（必须匹配 `vendor/` 里 wheel 的 cp 标签，目前是 **cp311**）：

1. `-PythonExe <path>`（用户显式指定，最高优先级）
2. **UE 自带 Python**：`%UE_ROOT%\Engine\Binaries\ThirdParty\Python3\Win64\python.exe`，默认依次探测 `UE_5.7 / 5.6 / 5.5 / 5.4`
3. `py -3.11` 启动器（Windows Python Launcher）
4. PATH 上的 `python` / `python3`

发现的解释器若与 vendor 标签不匹配（例如系统是 3.12，vendor 是 cp311），脚本会报错并打印安装/指定提示，**不会**用错版本继续构建。已存在的 `.venv` 若版本不匹配会自动重建。

> **常见坑**：如果 `pip list` 在 venv 里只看得到 `pip / setuptools`，说明依赖没装上 → 直接 `deploy.bat -ForceRecreate` 重新跑即可。脚本默认会做 import 校验，缺包时立即失败并提示。


### 2.2 手动注册到非 VS Code 客户端

**Codex** (`%USERPROFILE%\.codex\config.toml`)：路径必须绝对，按本机替换 `<RepoRoot>` / `<MemoryRelToRepo>`：

```toml
[mcp_servers.project-memory-mcp]
command = '<RepoRoot>/<MemoryRelToRepo>/.venv/Scripts/python.exe'
args = ['-m', 'servers.memory_server', '--root', '<RepoRoot>']
env = { PYTHONPATH = '<RepoRoot>/<MemoryRelToRepo>', PYTHONUTF8 = '1' }
```

也可只跑 `<MemoryRoot>/setup_mcp.ps1`（仅写 `.vscode/mcp.json`，不装依赖、不写 user id）。

修改 MCP 配置后需重启客户端或新开会话。

### 2.3 验证 / CLI 模式

```powershell
# 启动 MCP server（前台调试）
powershell -ExecutionPolicy Bypass -File <MemoryRoot>/scripts/run_memory_server.ps1

# 跑全部测试，应输出 506 passed
powershell -ExecutionPolicy Bypass -File <MemoryRoot>/scripts/run_memory_all_tests.ps1

# CLI 模式：不依赖 MCP 客户端，可用于 shell / CI
$env:PYTHONPATH = '<MemoryRoot>'
<MemoryRoot>/.venv/Scripts/python.exe -m servers.memory_server.cli --root <RepoRoot> guard --pretty
<MemoryRoot>/.venv/Scripts/python.exe -m servers.memory_server.cli --root <RepoRoot> health
<MemoryRoot>/.venv/Scripts/python.exe -m servers.memory_server.cli --root <RepoRoot> backup --path memory-bank/progress.md
```

CLI 子命令：`guard / health / rebuild-index / scale-baseline / auto-maintenance / migrate / backup / compact / validate / publish`，详见 [`servers/memory_server/cli.py`](./servers/memory_server/cli.py)。

### 2.4 Git/SVN ignore（团队接入必做）

漏配会出现高频不可自动合并冲突。

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

SVN 团队：**不要**对 `progress.md` / `techContext.md` / `systemPatterns.md` / `projectbrief.md` 加 `svn lock`，服务端已 append-only。

---

## 3. 使用方式

### 3.1 4 个 facade 工具

| 工具 | 用途 |
|---|---|
| `memory_read` | 读文件 / 搜记忆 / 搜结构化记录 / 读 runtime digest |
| `memory_write` | 写文件 / 写记录 / observation / 关联 artifact / 可选 LLM 蒸馏 |
| `memory_context` | 编译视图 / 谱系 / 冲突 / 上下文装配 / 关键文档重建 |
| `memory_enhance` | 6 个 read-only LLM 能力（classify / extract / merge / skill / conflict / handoff） |

每个工具靠 `operation` 字段分发。返回值统一含 `request_id`（UUID7，幂等）+ `new_sha`（可作下次 `if_match`）。

### 3.2 推荐工作流

> 核心规则：**只往 raw 写事实**。`activeContext.md` / `progress.md` / `techContext.md` / `systemPatterns.md` 是派生视图，由 `rebuild_key_documents` 重建。

1. **会话开始**：`memory_read.get` 拉 `memory-bank/activeContext.md` + `.ai-context/current-task.md`。
2. **过程中**：检索用 `memory_read.search`；落事实用 `memory_write.record`（observation / decision / incident / note / candidate），写一次即冻结。
3. **会话结束**：`memory_context.rebuild_key_documents` 自动重建关键文档。`renderer="auto"` 走 `["llm","deterministic"]`，LLM 不可用自动回落。

派生文档头部强制写 `<!-- generated_by=memory-mcp ... -->`，git diff 可识别。手写视为快速通道，下次 rebuild 前自动归档到 `memory-bank/archive/manual-edits/`。

纯手写工作流：`.ai-memory/config.json` 设 `key_documents.mode="manual"` 或 `"disabled"`。

### 3.3 多人协作

默认开启。`activeContext.md` 按 user 分文件；共享文件 append-only；`scope=personal/user_private` 在 compile 与 retrieval 双路径作者隔离。用户名优先级：`.vscode/settings.json` 的 `memory-mcp.userName` → `USERNAME`/`USER` → `unknown`。

并发：`if_match`（SHA-256 ETag）乐观锁，冲突返回 `error="conflict"` + `current_sha`。

### 3.4 LLM 接入（可选）

LLM 是**可选插件式蒸馏器**，不在主路径上。关闭 LLM 时所有写入 / 检索 / 编译 / deterministic 重建均正常。

```powershell
Copy-Item <MemoryRoot>/llm_config.example.json <MemoryRoot>/llm_config.local.json
# 填 api_key（已 gitignore），也支持 env：MEMORY_LLM_API_KEY / MEMORY_LLM_BASE_URL / MEMORY_LLM_MODEL
```

接入点：`memory_write.record` 加 `distill=true`、`memory_context.retrieve_context` 加 `summarize=true`、`memory_context.rebuild_key_documents` 走 `renderer="auto"|"llm"`、`memory_enhance` 6 个 read-only 能力（不写盘）。

---

## 4. 项目设计思想

1. **真源 = Markdown，数据库只是索引**。`memory-bank/**/*.md` 是唯一真源；`.ai-memory/search.db` / `compile-cache/` / `memory-bank/compiled/` 全部派生，删了能重建。
2. **写入安全 > 写入便利**。每次写入：白名单校验 → 跨进程文件锁 → 预算检查 → 备份 → 同目录 tmp + `O_CREAT|O_EXCL` + `fsync`（数据 fd + parent dir）→ `os.replace` 原子替换 → 审计（`request_id` / `new_sha`）。
3. **Deterministic > 智能**。编译、评分、谱系都是确定性纯函数。`importance = governance + usage + impact + novelty + conflict + decay`。LLM/RAG 只做软增强。
4. **预算优先 > top_k**。`memory_context.important_memories` 接受 `max_tokens/max_chars/max_items`，输出装得下的最重要记忆 + `evidence_refs` + `dropped_candidates` + `budget_report`。
5. **关键文档 = 派生视图**。raw 永远 `immutable=True`；4 份派生文档由 `rebuild_key_documents` 三档降级渲染（**LLM → embedding → deterministic**），任意档失败自动跌落，全部失败回滚 backup。
6. **多人协作默认开启**。activeContext 按 user 分文件，共享 append-only，作者隔离。
7. **Facade 收敛 MCP 表面**。默认只 4 个 facade，admin/legacy 走 CLI 与显式开关。
8. **LLM 只做 LLM 擅长的**。判断标准：非 LLM 已经够好 → 不引入；离开 LLM 做不好 → 引入但落 distilled 层不污染 raw。能力归属在 [`memory_llm_policy.py`](servers/memory_server/memory_llm_policy.py) 单一登记。

| 锁死为非-LLM | 由 LLM 做 | hybrid |
|---|---|---|
| raw 写入 / 哈希 / 冻结 | 自然语言摘要 | 关键文档重建 |
| Front Matter / Schema | 跨 raw 主题聚类 | 冲突识别 |
| 事件日志 / Lock / Backup | 自由文本重写 | 自然语言查询解析 |
| FTS / 评分 / 衰减 / 谱系 / 治理 / 预算 |  |  |

任意环节关闭 LLM 不退化为 0：raw 仍写、FTS 仍检索、deterministic 仍出 digest 与关键文档。

---

## 5. 开发历史与计划

完整版本流水见 [DEVLOG.md](./DEVLOG.md)。

### 计划中

1. **embedding 档（RAG）**：本地向量索引落 `.ai-memory/`，检索 `metadata → FTS → vector → rerank`，同步给 `renderer="embedding"` 复用（目前该档返回 `not_implemented`）。
2. **`compiled/snapshots/<doc>-<ts>.md` 显式回滚**：在 `backups/pre_rebuild` + atomic write 之外补独立时间戳子目录。
3. **`bootstrap.ps1` e2e 演练**：在真实开发机（含遗留 `mcp.json`/`settings.json`）端到端跑一遍并回写指纹。
