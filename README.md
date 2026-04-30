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
- **测试**：624 passed + 3 skipped（onnxruntime e2e gated）；并发/多进程/多用户子集 64 全绿。

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

# 跑全部测试，应输出 624 passed + 3 skipped
powershell -ExecutionPolicy Bypass -File <MemoryRoot>/scripts/run_memory_all_tests.ps1

# CLI 模式：不依赖 MCP 客户端，可用于 shell / CI
$env:PYTHONPATH = '<MemoryRoot>'
<MemoryRoot>/.venv/Scripts/python.exe -m servers.memory_server.cli --root <RepoRoot> guard --pretty
<MemoryRoot>/.venv/Scripts/python.exe -m servers.memory_server.cli --root <RepoRoot> health
<MemoryRoot>/.venv/Scripts/python.exe -m servers.memory_server.cli --root <RepoRoot> backup --path memory-bank/progress.md
```

CLI 子命令：`guard / health / rebuild-index / scale-baseline / auto-maintenance / migrate / backup / compact / validate / publish / archive / delete / compile / snapshot-rebuild / runtime-digest / rebuild-key-docs`，详见 [`servers/memory_server/cli.py`](./servers/memory_server/cli.py)。

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

默认开启。`activeContext.md` 按 user 分文件；共享文件 append-only；`scope=personal/user_private` 在 compile 与 retrieval 双路径作者隔离。

**用户名解析优先级**（v0.10.1 起）：

1. `MEMORY_MCP_USER` 环境变量（CI / 子进程 / 测试稳定注入；最高优先级）
2. `.vscode/settings.json` 的 `memory-mcp.userName`
3. `USERNAME`（Windows）/ `USER`（POSIX）
4. 兜底 `unknown`

**前置条件**：团队接入必须在 bootstrap 时确认 (1)/(2)/(3) 至少一个落到稳定值；否则 `memory_write` / `memory_write_record` 立即返回 `error="user_not_configured"` + `setup_hint`，**不写盘不备份**。常见失败模式：

| 场景 | 现象 | 处理 |
|---|---|---|
| 全新克隆未跑 bootstrap | 首次写入 `user_not_configured` | 跑 `bootstrap.ps1` 或手动写 `.vscode/settings.json["memory-mcp.userName"]` |
| CI / 子进程 / 容器无 `.vscode/settings.json` | 读 `USERNAME` 拿到 `runner` / 空 | 在调用进程注入 `MEMORY_MCP_USER=ci-runner` |
| 共享 OS 账号（`Administrator` / `root` …） | 写入成功但带 `warning="user_ambiguous"` | 设个人 user id 或忽略警告 |
| 只读 / 临时演示环境 | 不希望被强校验阻断 | `.ai-memory/config.json` 设 `mcp.allow_unknown_user=true` |

**并发**：`if_match`（SHA-256 ETag）乐观锁，冲突返回 `error="conflict"` + `current_sha`；跨进程文件锁 + atomic write 保证 N 个 MCP server 进程同时写同一文件不撕裂。覆盖：`test_concurrent_writes` / `test_multi_agent_stress` / `test_multi_user` / `test_user_validation` / `test_shared_overwrite_rejected` 共 64 个用例全绿。

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

### 已落地（最近）

- **v0.11.1 — v0.11.x 收口**：`scripts/download_embedding_model.py` 的内置 presets 已填实 `model + tokenizer + sha256`（`bge-small-zh-v1.5` 含 ONNX external data sidecar；`paraphrase-multilingual-MiniLM-L12-v2` 使用单文件 int8 ONNX），新增 `--list` 显示 verified 清单；新增 `scripts/llm_smoke.py`，由 `MEMORY_LLM_SMOKE=1` + 真 key 手动触发，覆盖 `distill_summary / query_rewrite / snapshot_narrative` 三个 capability 并输出 `status / latency_ms / token_used`。详见 DEVLOG。
- **v0.11.0 — RAG 召回质量解锁 + LLM 调用统一（首批）**：`LocalOnnxProvider` 引入真实 tokenizer（HuggingFace `tokenizers` / `sentencepiece`，缺失则透明降级到 deterministic-hash）；`_vector_supplement` 异常路径写 `events.jsonl::vector_supplement_skipped` + `memory_health_check.vector_skip_count_24h`；`scripts/eval_recall.py` 输出 `recall@5/10 + MRR + provider_id + model_hash`；三处 LLM 入口（`distill_summary` / `summarize_recall` / `rebuild_key_document`）统一改走 `run_llm_capability`；`config_diagnose` 新增 `llm_capabilities` 段（5 能力 × 4 字段，每字段带 `{value, source}`）。详见 DEVLOG。
- **v0.7.5 / v0.8.0 / v0.9.0 — embedding 档（RAG）Phase 1 / 2a / 2b / 2c**：本地向量索引落 `.ai-memory/vector_index/<provider>__<model_hash>/`；检索顺序 `metadata → FTS → vector supplement → rerank`；`renderer="embedding"` 已可用；`LocalOnnxProvider`（CPU EP only）+ `DeterministicHashProvider` 永久兜底；`scripts/download_embedding_model.py` 强制 sha256 校验。
- **v0.10.0 — P4 LLM 软增强收尾**：`memory_query_rewrite` + `memory_snapshot_narrative` + `memory_llm_runner` 七状态包络 + `llm_defaults` capability 粒度配置；CLI `weekly-snapshot-rebuild` / `monthly-snapshot-rebuild --narrative`。默认 `enabled=False`，老链路零行为变化。
- **v0.10.1 — 团队接入扫尾**：`MEMORY_MCP_USER` 环境变量成为最高优先级 user 来源（CI / 子进程 / 测试稳定注入）；§3.3 显式列出失败模式与降级开关；并发/多进程子集 64 用例确认全绿。

### 已完成里程碑详情（设计文档原 §15.1–§15.4 细节归档）

#### v0.6.0 — 开箱即用稳健性（OOTB）

总目标：使用者唯一显式配置 = 自己的 user id；其余团队约定全部由插件自动兜底。

**P0 — 数据安全**：
- `memory_users.py`：`is_placeholder_user` 拒绝 `""` / `unknown` / 含路径分隔符；模糊用户名（`Administrator` 等）写 `user_ambiguous` warning；返回结构化 `error="user_not_configured"` + `setup_hint`。
- `memory_writer.py`：`shared_paths_policy=append_only` + `mode="overwrite"` 立即返回 `error="shared_overwrite_forbidden"` + `suggested_operation="record"` + `suggested_mode="append"`，不写盘不备份；遗留静默降级行为退化为 opt-in (`mcp.shared_overwrite_policy="downgrade"`)。
- `memory_auto_maintenance.py`：`run_if_due(config)` 检查 `last_maintenance.json`，超阈值（默认 `min_interval_hours=168` / `events_max_bytes=50MB` / `index_stale_seconds=600`）按需触发 health/rebuild/compact；幂等、失败不阻塞。

**P1 — 易用性**：
- `bootstrap.ps1`：venv → 装依赖 → 询问 user id（幂等）→ 写 `.vscode/mcp.json`+`settings.json` → 跑一次 health。
- UE facet 自动推断（`memory_ue_facets.py`）：见设计文档 §17。
- shared append auto-compact（`memory_shared_compactor.py`）：超阈值（默认 2000 行）自动 fold 旧条目到 `memory-bank/archive/<basename>-YYYYWW.md`。
- `memory_context.config_diagnose`：报告每条策略来源（默认 / 文件 / 环境变量）。
- `link_artifact` 自动归一化：UE `/Game/X` ↔ 物理 `Content/X.uasset` 双向解析 + 附 `git_sha`。

**P2 — 观测**：
- `cli scale-baseline`：跑 smoke 写 `.ai-memory/baseline.json`；health 对比基线，回归默认 2× 因子触发 `scale_regression` issue。
- health 启动自愈（`memory_maintenance._self_heal`）：异常时清 60s 以上的 `*.tmp` 孤儿与陈旧 `.lock` sidecar，结果含 `self_heal: {tmp_removed, stale_locks_removed}`。
- 策略 schema 哈希一致性（`memory_strategy_hash.py`）：不一致时写 `events.jsonl` 警告。

#### v0.7.0 — P4-C 关键文档可重建

`activeContext.md` / `progress.md` / `techContext.md` / `systemPatterns.md` 能从结构化记录、snapshot、observation 中确定性重建；误操作、合并冲突、跨机同步丢失后可一键恢复。

- `KEY_DOCUMENTS` manifest（`memory_key_documents.py`）：列出可重建的目标文件及其证据来源（record kinds / scopes / facet 查询）。
- facade：`memory_context(operation="rebuild_key_documents", targets=[…], renderer="auto"|"deterministic"|"llm"|"embedding")`。
- CLI：`python -m servers.memory_server.cli rebuild-key-docs`，执行前自动 `backup_files` 原文件。
- 三档 renderer：`deterministic`（无 LLM 完整可用）/ `llm`（缺则 `error="llm_unavailable"`）/ `embedding`（缺索引则 `error="embeddings_disabled"`）。
- 必须保留 backup，返回 `request_id` / `archived_manual_edit_to`（人工编辑前先归档到 `memory-bank/archive/manual-edits/`）。

#### v0.7.5 / v0.8.0 / v0.9.0 — RAG（向量补召回）Phase 1 → 2c

**硬约束**（与「无 LLM/网络依赖」一致）：
- 必须本地小模型（≤ 200MB，bge-small-zh / MiniLM 等），ONNX Runtime 加载，禁止默认调用远程 embedding API。
- 必须纯 CPU 可跑：只允许 `onnxruntime` CPU EP；禁止 `torch` / CUDA 列为强依赖。
- 可选 + 可降级：模型缺失/加载失败时整条 vector 档跳过，回落到 metadata + FTS。
- 离线/可复现：模型文件入 `.ai-memory/models/`，不在运行时联网下载。
- 资源上限：单次 embed 批次、向量维度、索引大小有上限配置。

**Provider 抽象**：

```
EmbeddingProvider
├── DeterministicHashProvider  ← 内置零依赖，64 维 hash 向量；测试基线 + 永久兜底
├── LocalOnnxProvider          ← bge-small / MiniLM 等，onnxruntime CPU EP only
├── LocalGpuProvider           ← Phase 3 可选，检测到 CUDA EP 才注册
└── RemoteApiProvider          ← 不实现
```

`LocalOnnxProvider` 启动时强制 `providers=["CPUExecutionProvider"]`；`model_hash` = 模型文件 sha256 截断 16 字符，模型替换时索引目录自动失效。

**索引格式**：`.ai-memory/vector_index/<provider_id>__<model_hash>/`
- `meta.json`：`{provider_id, model_hash, dim, normalized, count, created_at}`
- `vectors.bin`：raw float32 / int8 量化二进制
- `ids.jsonl`：每行 `{record_id, chunk_id, source_path, text_preview}`
- provider 或 model_hash 变更 → 整目录失效，下次启动触发重建。

**接入点**：
- `memory_retrieval._vector_supplement`：`metadata + FTS` 之后，若 `embeddings_enabled` 为真则调用 `vector_search(top_k=50)`；命中候选集时 `match_score < 0` 按 `0.25 × cos` 提升至候选，`match_score ≥ 0` 时叠加 `0.10 × cos` 加成（封顶 `0.5`）。任何异常都被 try/except 吞掉，主路径永不阻塞。
- `memory_key_documents.render_embedding_document`：用 spec 的 title/role/tags/kinds 拼成查询，调 `vector_search` 把候选集语义重排后套同款模板渲染，meta 行追加 `vector_score=`。`renderer="embedding"` 自动追加 `deterministic` 兜底。

**配置**：

```json
{
  "embeddings": {
    "enabled": false,
    "provider": "auto",
    "model_path": ".ai-memory/models/bge-small-zh-v1.5/model_quantized.onnx",
    "max_batch": 32,
    "max_index_chunks": 100000,
    "rebuild_on_provider_change": true
  }
}
```

#### v0.10.0 — P4 LLM 软增强余项

- `memory_query_rewrite.rewrite_query` + `memory_retrieval._rank_records(extra_queries=…)` + `memory_context.{retrieve_context|important_memories}` 的 `rewrite_query` 开关；dispatch 走 `_run_query_rewrite`。
- `memory_snapshot_narrative.{generate_snapshot_narrative,inject_narrative}` + `compile_snapshot_target(narrative=True)` + CLI `weekly-snapshot-rebuild` / `monthly-snapshot-rebuild --narrative`。
- `memory_llm_runner.run_llm_capability` + `memory_config.llm_defaults`（capability 粒度覆盖）+ 七状态包络（`ok / disabled / unavailable / timeout / budget_exceeded / failed / invalid_capability`）+ fallback 保留原始 status 供诊断。

通用约束：不直接发布正式系统记忆；不覆盖真源；不替代 deterministic compile；无 LLM 时基础链路完整可用。

#### v0.10.1 — 团队接入扫尾

| 入口 | 实现 |
|---|---|
| 环境变量最高优先级 | `memory_events.get_current_user` 增 `MEMORY_MCP_USER` 检查（空/纯空白表示不覆盖） |
| 只读/临时环境 | `mcp.allow_unknown_user=true`（已存在，§3.3 显式列出） |
| 贯穿测试/CI | `tests/memory_server/conftest.py` autouse fixture 清理 `MEMORY_MCP_USER`，限定只在显式 setenv 的测试内生效 |

### 计划中

1. **`compiled/snapshots/<doc>-<ts>.md` 显式回滚**：在 `backups/pre_rebuild` + atomic write 之外补独立时间戳子目录。
2. **`bootstrap.ps1` e2e 演练**：在真实开发机（含遗留 `mcp.json`/`settings.json`）端到端跑一遍并回写指纹。
3. **RAG Phase 3**（GPU EP / 量化 / HNSW）：仅当 `chunks ≥ 100k / 全量重建 ≥ 10min / QPS ≥ 20` 任一阈值命中时启动；当前规模未达，不启动。
