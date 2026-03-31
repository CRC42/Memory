# 通用 Memory MCP Server（v0.4.0）

基于 MCP SDK 的独立 Python 服务器，为 AI 提供结构化项目记忆管理能力：读取、搜索、guard 容量监控、备份轮转、规则压缩、受控写入。支持十人级团队多人协作（用户分区 + 冲突消除）。

## 工具一览

| 工具 | 功能 | 关键特性 |
|------|------|----------|
| `memory_get` | 读取记忆文件内容 | 行范围选取、字符截断、多人模式自动重定向到用户分区 |
| `memory_search` | 关键词搜索（支持 CJK） | 标题加权评分、±2 行上下文窗口 |
| `memory_guard_check` | 容量监控 | 逐文件阈值 + 全局总预算（chars/tokens）、用户分区目录扫描 |
| `memory_backup` | 备份记忆文件 | 自动轮转（max_batches + max_total_bytes） |
| `memory_compact` | 规则压缩（默认 dry_run） | 3 种策略：hot_task / error_summary / warm_context |
| `memory_write` | 受控写入 | 原子写入、自动备份、路径白名单、全局预算拒绝、用户分区重定向、write_policy 强制降级 |

## 目录结构

```text
MCP/Memory/
├── servers/memory_server/   # 服务端源码（15 个模块）
│   ├── server.py            # 入口、工具定义（动态生成）、请求分发
│   ├── memory_config.py     # 配置加载、数据类、默认值、multi_user 解析
│   ├── memory_reader.py     # memory_get 实现（含用户分区重定向）
│   ├── memory_search.py     # memory_search 实现（CJK 分词）
│   ├── memory_guard.py      # guard_check + 全局预算 + 用户分区目录扫描
│   ├── memory_backup.py     # 备份 + 轮转
│   ├── memory_compactor.py  # 规则压缩（3 策略）
│   ├── memory_writer.py     # 受控写入（原子 + 审计 + write_policy + 用户标签）
│   ├── memory_paths.py      # 路径安全管理 + 用户分区重定向 + 自动迁移
│   ├── memory_events.py     # 审计日志（文件锁）+ 用户身份获取
│   ├── memory_result.py     # 统一返回格式
│   ├── token_estimator.py   # CJK 感知 token 估算
│   ├── __init__.py
│   └── __main__.py
├── tests/memory_server/     # pytest 测试（44 用例）
│   ├── conftest.py
│   ├── test_security_and_get.py
│   ├── test_search.py
│   ├── test_guard.py
│   ├── test_backup.py
│   ├── test_compact.py
│   ├── test_write.py
│   ├── test_dispatch.py
│   └── test_budget_rotation_dynamic.py
├── scripts/                 # 运行 & 测试脚本
├── vendor/                  # 离线依赖包
├── requirements.txt
├── requirements-dev.txt
├── deploy.ps1
├── setup_mcp.ps1
├── DEVLOG.md
└── README.md
```

## 配置

配置文件：`.ai-memory/config.json`（不存在时自动生成默认值）

### 核心配置项

```json
{
  "allowed_roots": [".ai-context", "memory-bank"],
  "multi_user": {
    "enabled": true,
    "user_scoped_paths": [
      "memory-bank/activeContext.md"
    ],
    "shared_paths_policy": {
      "memory-bank/progress.md": "append_only",
      "memory-bank/techContext.md": "append_only",
      "memory-bank/systemPatterns.md": "append_only",
      "memory-bank/projectbrief.md": "append_only"
    }
  },
  "backup": {
    "max_total_bytes": 524288,
    "max_batches": 5
  },
  "guard": {
    "total_max_chars": 150000,
    "total_max_tokens": 40000,
    "targets": [
      {
        "path": "memory-bank/activeContext.md",
        "max_chars": 8000,
        "policy": "warm_context",
        "role": "current sprint focus, recent decisions, TODOs",
        "write_policy": "user_scoped"
      },
      {
        "path": "memory-bank/progress.md",
        "max_chars": 12000,
        "policy": "warm_context",
        "role": "feature completion status, milestones",
        "write_policy": "append_only"
      }
    ]
  }
}
```

### 配置说明

| 配置项 | 说明 |
|--------|------|
| `allowed_roots` | 可读写的目录白名单 |
| `multi_user.enabled` | 是否启用多人协作模式 |
| `multi_user.user_scoped_paths` | 需要按用户分区的文件路径列表 |
| `multi_user.shared_paths_policy` | 共享文件的写入策略（`append_only` = 强制 append） |
| `backup.max_batches` / `max_total_bytes` | 备份轮转上限 |
| `guard.total_max_chars` / `total_max_tokens` | 全局记忆预算（v0.4.0 扩容至 150K/40K） |
| `guard.targets[].role` | 文件角色描述（动态注入到 MCP 工具提示词中） |
| `guard.targets[].write_policy` | 写入策略：`user_scoped`（用户分区）/ `append_only`（强制追加） |

## 记忆文件说明

### 核心文件（`memory-bank/`）

| 层级 | 文件 | 角色 | 何时读取 |
|------|------|------|----------|
| L0 必读 | `activeContext/{user}.md` | 当前工作焦点、近期决策、待办（每人独立） | 每次会话开始 |
| L1 按需 | `progress.md` | 功能状态、里程碑、完成度 | 涉及进度/验收时 |
| L2 按需 | `techContext.md` | 技术栈、插件矩阵、架构配置 | 涉及技术方案时 |
| L3 按需 | `systemPatterns.md` | 架构模式、编码约定、设计决策 | 涉及实现规范时 |
| L4 极少 | `projectbrief.md` | 项目定义、核心需求、MVP 目标 | 讨论方向与需求时 |

> **注意**：`activeContext.md` 在多人模式下自动重定向到 `activeContext/{user}.md`，调用方无需手动指定用户名。

### 热上下文（`.ai-context/`）

| 文件 | 角色 | 典型操作 |
|------|------|----------|
| `current-task.md` | 当前任务临时上下文 | `memory_get` / `memory_write` / `memory_compact(policy=hot_task)` |
| `latest-error.md` | 最新错误摘要 | `memory_get` / `memory_write` / `memory_compact(policy=error_summary)` |

### 基础设施（`.ai-memory/`）

| 路径 | 用途 |
|------|------|
| `config.json` | 配置文件（guard 阈值、备份策略、文件角色、多人协作） |
| `events.jsonl` | 写操作审计日志（自动记录） |
| `backups/` | 历史备份（按日期/批次，自动轮转） |
| `temp/` | 原子写入临时目录 |

## 多人协作（v0.4.0）

### 核心机制

#### 1. 用户分区写入（零 Git 冲突）

`activeContext.md` 从单文件改为按用户分区目录：

```
memory-bank/
  activeContext/              ← 目录替代单文件
    mengzhoyang.md            ← 个人活跃上下文（overwrite）
    zhangsan.md               ← 个人活跃上下文（overwrite）
    ...                       ← 每人一个文件，互不干扰
  progress.md                 ← 共享（append_only）
  techContext.md              ← 共享（append_only）
```

- 调用方仍使用 `memory_get("memory-bank/activeContext.md")`，MCP 自动重定向到 `activeContext/{user}.md`
- 每人写自己的文件，Git 合并**零冲突**

#### 2. 写入策略强制（write_policy）

| write_policy | 行为 | 适用文件 |
|-------------|------|----------|
| `user_scoped` | 自动重定向到 `{dir}/{user}.md` | `activeContext.md` |
| `append_only` | overwrite 请求自动降级为 append | `progress.md`、`techContext.md`、`systemPatterns.md`、`projectbrief.md` |
| 无 / null | 保持原有行为 | `.ai-context/` 下的文件 |

#### 3. 用户标签注入（可追溯性）

- **overwrite 模式**：文件末尾自动追加尾注
  ```
  <!-- last overwritten by mengzhoyang at 2026-03-31 12:00 UTC -->
  ```
- **append 模式**：追加内容前自动插入标识头
  ```
  <!-- written by mengzhoyang at 2026-03-31 12:00 UTC -->
  ```

#### 4. 自动迁移

首次访问时，如果检测到旧的 `activeContext.md` 单文件存在且新的用户分区文件不存在，自动将旧文件内容复制到 `activeContext/{user}.md`（旧文件保留不删除，供其他用户迁移）。

### 用户身份获取（完全无感 + 可配置覆盖）

用户名获取优先级：

```
1. .vscode/settings.json → "memory-mcp.userName"  （显式配置，最高优先）
2. 环境变量 USERNAME (Windows) / USER (POSIX)      （系统 API 自动获取，零配置）
3. 回退到 'unknown'                                （兜底）
```

- **大多数场景**（每人独立电脑）：**零配置**，系统用户名自动区分
- **可选覆盖**：在 `.vscode/settings.json` 中配置自定义用户名（优先级最高）：
  ```json
  {
      "memory-mcp.userName": "mengzhoyang"
  }
  ```
  - `.vscode/` 已在 `.gitignore` 中排除，不进 Git，每人独立配置
  - VSCode 对未知 key 完全忽略，不影响 IDE 本身

### Git 共享策略

| 路径 | 进 Git？ | 说明 |
|------|----------|------|
| `memory-bank/*.md` | ✅ 是 | 全团队共享的项目记忆 |
| `memory-bank/activeContext/*.md` | ✅ 是 | 每人独立文件，零冲突 |
| `.ai-memory/config.json` | ✅ 是 | 共享配置 |
| `.ai-memory/events.jsonl` | ❌ 否 | 运行时审计日志（本地产生） |
| `.ai-memory/backups/` | ❌ 否 | 运行时备份（本地产生） |
| `.ai-memory/temp/` | ❌ 否 | 原子写入临时文件 |
| `.ai-context/` | ❌ 否 | 个人临时上下文 |

### Git 冲突分析（10 人团队）

| 文件 | 写入频率 | 写入模式 | 冲突概率 |
|------|----------|----------|----------|
| `activeContext/{user}.md` | 高（每次会话） | overwrite | **零**（每人独立文件） |
| `progress.md` | 低 | append（强制） | **极低**（append 天然可合并） |
| `techContext.md` | 低 | append（强制） | **极低** |
| `systemPatterns.md` | 低 | append（强制） | **极低** |
| `projectbrief.md` | 极低 | append（强制） | **几乎为零** |

## 安全机制

- **路径白名单**：仅 `allowed_roots` 下的文件可被读写
- **原子写入**：temp file → `os.replace`，避免写入中断导致数据损坏
- **自动备份**：`memory_write` 写入前自动备份原文件
- **全局预算**：写入时检查所有记忆文件总大小，超限则拒绝写入（v0.4.0 扩容至 150K chars）
- **逐文件 guard**：每个文件独立的 chars/tokens 阈值
- **备份轮转**：按 batch 数和总大小自动清理旧备份
- **审计日志**：每次写操作记录到 `events.jsonl`（带文件锁、自动记录操作者用户名）
- **write_policy 强制**：`append_only` 文件无法被 overwrite，自动降级为 append
- **用户分区隔离**：`user_scoped` 文件自动重定向到个人分区，防止互相覆盖

## 部署

```powershell
# 仅创建虚拟环境
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1

# 创建虚拟环境 + 运行时依赖
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1 -InstallDeps

# 创建虚拟环境 + 运行时依赖 + 开发/测试依赖
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1 -InstallDevDeps

# 创建虚拟环境并自动注册到 .vscode/mcp.json
powershell -ExecutionPolicy Bypass -File MCP/Memory/deploy.ps1 -RegisterVSCode
```

## VS Code MCP 配置

```powershell
# 自动注册到 .vscode/mcp.json
powershell -ExecutionPolicy Bypass -File MCP/Memory/setup_mcp.ps1
```

配置示例（`.vscode/mcp.json`）：

```json
{
  "servers": {
    "project-memory-mcp": {
      "command": "D:/Git/P111/MCP/Memory/.venv/Scripts/python.exe",
      "args": ["-m", "servers.memory_server", "--root", "D:/Git/P111"],
      "env": {
        "PYTHONPATH": "D:/Git/P111/MCP/Memory",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

## 测试

```powershell
# 全量测试（44 用例）
./MCP/Memory/scripts/run_memory_all_tests.ps1

# 或直接使用 pytest
cd MCP/Memory && .venv/Scripts/python.exe -m pytest tests/ -v --tb=short
```

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.4.0 | 2026-03-31 | **多人协作 v2**：用户分区写入（activeContext/{user}.md）、write_policy 强制（append_only 降级）、overwrite 尾注标签、自动迁移、全局预算扩容至 150K、guard 适配目录扫描、44 测试全通过 |
| v0.3.1 | 2026-03-24 | 用户名可配置覆盖：`.vscode/settings.json` 优先读取、进程级缓存、向后兼容 |
| v0.3.0 | 2026-03-24 | 多人协作支持：用户自动标识、审计日志注入用户、append 用户头、Git 共享策略 |
| v0.2.0 | 2026-02-25 | 备份轮转、全局记忆预算、动态工具描述、42 测试 |
| v0.1.1 | 2026-02-25 | 新增 `memory_write`、健壮性修复（CJK 搜索、文件锁、token 估算）、30 测试 |
| v0.1.0 | 2026-02-25 | Phase 1 MVP：5 工具、7 测试 |

详细变更日志见 [DEVLOG.md](DEVLOG.md)。
