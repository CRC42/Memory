# 通用 Memory MCP Server（v0.2.0）

基于 MCP SDK 的独立 Python 服务器，为 AI 提供结构化项目记忆管理能力：读取、搜索、guard 容量监控、备份轮转、规则压缩、受控写入。

## 工具一览

| 工具 | 功能 | 关键特性 |
|------|------|----------|
| `memory_get` | 读取记忆文件内容 | 行范围选取、字符截断 |
| `memory_search` | 关键词搜索（支持 CJK） | 标题加权评分、±2 行上下文窗口 |
| `memory_guard_check` | 容量监控 | 逐文件阈值 + 全局总预算（chars/tokens） |
| `memory_backup` | 备份记忆文件 | 自动轮转（max_batches + max_total_bytes） |
| `memory_compact` | 规则压缩（默认 dry_run） | 3 种策略：hot_task / error_summary / warm_context |
| `memory_write` | 受控写入 | 原子写入、自动备份、路径白名单、全局预算拒绝 |

## 目录结构

```text
MCP/memory/
├── servers/memory_server/   # 服务端源码（14 个模块）
│   ├── server.py            # 入口、工具定义（动态生成）、请求分发
│   ├── memory_config.py     # 配置加载、数据类、默认值
│   ├── memory_reader.py     # memory_get 实现
│   ├── memory_search.py     # memory_search 实现（CJK 分词）
│   ├── memory_guard.py      # guard_check + 全局预算
│   ├── memory_backup.py     # 备份 + 轮转
│   ├── memory_compactor.py  # 规则压缩（3 策略）
│   ├── memory_writer.py     # 受控写入（原子 + 审计）
│   ├── memory_paths.py      # 路径安全管理
│   ├── memory_events.py     # 审计日志（文件锁）
│   ├── memory_result.py     # 统一返回格式
│   ├── token_estimator.py   # CJK 感知 token 估算
│   ├── __init__.py
│   └── __main__.py
├── tests/memory_server/     # pytest 测试（42 用例）
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
  "backup": {
    "max_total_bytes": 52428800,
    "max_batches": 50
  },
  "guard": {
    "total_max_chars": 60000,
    "total_max_tokens": 15000,
    "targets": [
      {
        "path": "memory-bank/activeContext.md",
        "max_chars": 8000,
        "policy": "warm_context",
        "role": "current sprint focus, recent decisions, TODOs"
      }
    ]
  }
}
```

- `allowed_roots`：可读写的目录白名单
- `backup.max_batches` / `backup.max_total_bytes`：备份轮转上限
- `guard.total_max_chars` / `guard.total_max_tokens`：全局记忆预算
- `guard.targets[].role`：文件角色描述（动态注入到 MCP 工具提示词中）

## 记忆文件说明

### 核心文件（`memory-bank/`）

| 层级 | 文件 | 角色 | 何时读取 |
|------|------|------|----------|
| L0 必读 | `activeContext.md` | 当前工作焦点、近期决策、待办 | 每次会话开始 |
| L1 按需 | `progress.md` | 功能状态、里程碑、完成度 | 涉及进度/验收时 |
| L2 按需 | `techContext.md` | 技术栈、插件矩阵、架构配置 | 涉及技术方案时 |
| L3 按需 | `systemPatterns.md` | 架构模式、编码约定、设计决策 | 涉及实现规范时 |
| L4 极少 | `projectbrief.md` | 项目定义、核心需求、MVP 目标 | 讨论方向与需求时 |

### 热上下文（`.ai-context/`）

| 文件 | 角色 | 典型操作 |
|------|------|----------|
| `current-task.md` | 当前任务临时上下文 | `memory_get` / `memory_write` / `memory_compact(policy=hot_task)` |
| `latest-error.md` | 最新错误摘要 | `memory_get` / `memory_write` / `memory_compact(policy=error_summary)` |

### 基础设施（`.ai-memory/`）

| 路径 | 用途 |
|------|------|
| `config.json` | 配置文件（guard 阈值、备份策略、文件角色） |
| `events.jsonl` | 写操作审计日志（自动记录） |
| `backups/` | 历史备份（按日期/批次，自动轮转） |
| `temp/` | 原子写入临时目录 |

## 安全机制

- **路径白名单**：仅 `allowed_roots` 下的文件可被读写
- **原子写入**：temp file → `os.replace`，避免写入中断导致数据损坏
- **自动备份**：`memory_write` 写入前自动备份原文件
- **全局预算**：写入时检查所有记忆文件总大小，超限则拒绝写入
- **逐文件 guard**：每个文件独立的 chars/tokens 阈值
- **备份轮转**：按 batch 数和总大小自动清理旧备份
- **审计日志**：每次写操作记录到 `events.jsonl`（带文件锁）

## 部署

```powershell
# 仅创建虚拟环境
powershell -ExecutionPolicy Bypass -File MCP/memory/deploy.ps1

# 创建虚拟环境 + 运行时依赖
powershell -ExecutionPolicy Bypass -File MCP/memory/deploy.ps1 -InstallDeps

# 创建虚拟环境 + 运行时依赖 + 开发/测试依赖
powershell -ExecutionPolicy Bypass -File MCP/memory/deploy.ps1 -InstallDevDeps

# 创建虚拟环境并自动注册到 .vscode/mcp.json
powershell -ExecutionPolicy Bypass -File MCP/memory/deploy.ps1 -RegisterVSCode
```

## VS Code MCP 配置

```powershell
# 自动注册到 .vscode/mcp.json
powershell -ExecutionPolicy Bypass -File MCP/memory/setup_mcp.ps1
```

配置示例（`.vscode/mcp.example.json`）：

```json
{
  "servers": {
    "project-memory-mcp": {
      "command": "C:/work/SVN/p110_2/MCP/memory/.venv/Scripts/python.exe",
      "args": ["-m", "servers.memory_server", "--root", "C:/work/SVN/p110_2"],
      "env": {
        "PYTHONPATH": "C:/work/SVN/p110_2/MCP/memory",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

## 测试

```powershell
# 全量测试（42 用例）
./MCP/memory/scripts/run_memory_all_tests.ps1

# 或直接使用 pytest
cd MCP/memory && .venv/Scripts/python.exe -m pytest tests/ -v --tb=short
```

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-02-25 | 备份轮转、全局记忆预算、动态工具描述、42 测试 |
| v0.1.1 | 2026-02-25 | 新增 `memory_write`、健壮性修复（CJK 搜索、文件锁、token 估算）、30 测试 |
| v0.1.0 | 2026-02-25 | Phase 1 MVP：5 工具、7 测试 |

详细变更日志见 [DEVLOG.md](DEVLOG.md)。
