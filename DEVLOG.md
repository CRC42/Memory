# DEVLOG - MCP Memory

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
