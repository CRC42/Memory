# DEVLOG - MCP Memory

## 2026-04-21 (vNext 设计稿: 从文件级 Memory MCP 走向记录级治理与编译架构)

> **状态：设计方案，未执行开发**

### 背景

`v0.4.0` 已经解决了多人协作下最现实的几个问题：

- `activeContext` 高频写入冲突
- 共享记忆文件的 append 策略
- 用户分区重定向
- guard / backup / compaction / 审计等基础设施

但当前系统的主抽象仍然是“文件”，而不是“记忆记录”。这会带来几个后续开发阶段绕不开的问题：

1. 系统记忆、个人记忆、候选记录、归档记录还没有统一的结构化外壳
2. 检索仍以文件粒度为主，尚未形成记录级索引与检索排序能力
3. 缺少候选验证、正式发布、降级归档这条治理链路
4. 缺少运行时 digest、handoff、publish queue 等“编译产物”层
5. MCP 对外能力仍偏向文件操作，尚未升级为记录级接口

因此，本次设计稿的目标不是推翻 `v0.4.0`，而是在兼容现有 `memory_get` / `memory_write` / `memory_search` / `memory_guard_check` 工作方式的前提下，补齐 Memory MCP 的长期架构。

### 本次设计结论

#### 1. 真源定义上移到“记录层”

系统真源不再仅仅理解为几份 Markdown 摘要文件，而是以下对象的组合：

- Markdown 记忆文件
- YAML Front Matter 元数据
- 事件日志
- 候选记录
- 已发布系统记忆

LLM 只能参与提炼、分类和填表，不能成为唯一真源。

#### 2. 记忆分层从 3 层扩展为 5 层

在当前 `memory-bank/`、`.ai-context/`、`.ai-memory/` 的基础上，设计稿将记忆职责明确拆分为：

- 个人记忆
- 系统记忆
- 历史记忆
- 本地临时记忆
- 编译记忆

这意味着后续实现不应再把所有长期知识都塞进少数几个共享 Markdown 文件，而应引入 `shared/`、`people/{user}/`、`candidates/`、`archive/`、`compiled/` 等正式目录层。

#### 3. 正式存储格式确定为 Markdown + Front Matter

本次设计明确了后续正式记录格式：

- MCP 传输层使用 JSON
- 落盘正文使用 Markdown
- 结构化元数据使用 YAML Front Matter
- 检索索引使用 SQLite FTS
- 审计与事件流使用 JSONL

这一步很关键，因为它把当前“文件写入工具”继续保留下来，同时为后续的记录级 schema、索引、编译和发布能力提供稳定底座。

#### 4. 正式引入“记忆编译”概念

设计稿将编译定义为一种确定性聚合过程，而不是让 LLM 再写一遍总结。编译输入优先依赖结构化字段，例如：

- `record_kind`
- `scope`
- `status`
- `tags`
- `source_refs`
- `confidence`
- `task_id`
- `branch`

编译输出初步划分为：

- `runtime digest`
- `task handoff`
- `system digest`
- `publish queue`

这意味着当前的 `activeContext.md`、`progress.md` 等文件，在长期演进里更适合作为“编译视图”或“人类可读摘要”，而不是唯一主真源。

#### 5. 正式引入候选治理与发布流

本次设计稿把系统记忆与 skill 治理流程统一定义为：

`raw -> candidate -> validated -> published -> degraded/archive`

同时明确以下边界：

- 允许自动创建候选、归档建议、编译视图
- 不允许 LLM 自动发布正式系统记忆
- 不允许 LLM 自动发布正式 skill
- 不允许 LLM 自动删除正式系统规则

这让后续 Memory MCP 能从“记录信息”升级到“治理知识”。

#### 6. 无 LLM 兜底能力被提升为硬要求

设计稿明确要求：即使没有 LLM，系统也必须完整支持：

- 原始记录写入
- Front Matter 解析
- 规则校验
- SQLite FTS 检索
- 模板式 digest 编译
- 候选验证与发布流程

换句话说，LLM 在后续架构中的定位是增强器，不是依赖前提。

### 目录模型调整建议

设计稿提出的 vNext 目标目录如下：

```text
memory-bank/
  shared/
  people/{user}/
  candidates/
  archive/
  compiled/
    runtime/
    publish/

.ai-context/{user}/

.ai-memory/
  config.json
  search.db
  events.jsonl
  compile-cache/
  temp/
  backups/
```

这里最重要的不是目录本身，而是职责变化：

- `shared/` 用于已发布系统记忆
- `people/{user}/` 用于个人长期沉淀
- `candidates/` 用于 system / skill 候选池
- `archive/` 用于降级和长期历史
- `compiled/` 用于运行时和发布流程所需的派生视图

### 与 v0.4.0 的关系

这次设计稿不是否定 `v0.4.0`，而是把 `v0.4.0` 放在更清晰的阶段定位上：

- `v0.4.0` 解决的是“多人协作下文件级记忆系统如何可用”
- 2026-04-21 设计稿解决的是“记忆系统如何从文件工具演进为长期治理系统”

因此后续实施应遵循以下顺序：

1. 先保留现有文件级接口，避免破坏当前工作流
2. 在此基础上增加记录级 schema、Front Matter 解析和 SQLite FTS 索引
3. 再补齐 compile / validate / publish / archive 能力
4. 最后才接入本地小模型或云端 LLM 做增强能力

### 对后续开发的直接约束

根据本次设计稿，后续继续开发 `ToolTest/MCP/Memory` 时应遵守以下约束：

1. 不再把少量共享 Markdown 文件当作唯一真源
2. 新能力优先围绕“记录级 schema”设计，而不是继续堆文件级特判
3. 所有 compile 结果必须可重建，不得成为唯一真源
4. 系统记忆正式发布必须经过候选验证，不允许 LLM 直接跳过治理流程
5. 任何增强能力都不得阻断无 LLM 的基础链路

### 产出

- 新增设计文档：`MemorySystemDesignDocument.md`
- 文档用途：作为 `MCP/Memory` 后续开发的 vNext 架构基线
- README 应继续维护“当前实现说明”，设计文档负责维护“下一阶段架构设计”

---

## 2026-03-31 (v0.4.0 设计稿: 十人团队多人协作 — 用户分区写入 + 冲突消除)

> **状态：设计方案，未执行开发**

### 背景与问题

团队即将扩展至 10 人规模，当前 Memory MCP 的多人协作支持存在以下瓶颈：

| # | 问题 | 严重度 | 影响范围 |
|---|------|--------|----------|
| 1 | `activeContext.md` 使用 overwrite 模式，10 人同时写入必然产生全文 Git 冲突 | **P0** | 每次 push/pull |
| 2 | overwrite 模式不注入任何用户标签，无法区分内容归属 | **P1** | 代码审查、冲突解决 |
| 3 | `.ai-context/` 不进 Git 但也无用户隔离，同一机器多人共用时互相覆盖 | **P1** | 共享开发机 |
| 4 | `config.json` 中 `preferred_mode: "append"` 在 DEVLOG v0.3.0 中提及但代码未实现 | **P2** | 配置与行为不一致 |
| 5 | 全局预算 60K chars 在 10 人场景下可能不够 | **P2** | 写入被拒绝 |

### 当前架构分析

```
memory-bank/              ← 进 Git，全团队共享
  activeContext.md         ← overwrite 模式，冲突高发区
  progress.md              ← 低频写入，冲突风险低
  techContext.md            ← 低频写入，冲突风险低
  systemPatterns.md         ← 低频写入，冲突风险低
  projectbrief.md           ← 极低频，冲突风险极低

.ai-context/              ← 不进 Git，个人临时上下文
  current-task.md          ← 个人任务，不冲突
  latest-error.md          ← 个人错误，不冲突

.ai-memory/               ← config.json 进 Git，其余不进
  config.json              ← 共享配置
  events.jsonl             ← 不进 Git
  backups/                 ← 不进 Git
  temp/                    ← 不进 Git
```

**核心矛盾**：`activeContext.md` 是写入最频繁的文件（每次会话结束都写），但使用 overwrite 模式，10 人 = 10 个 AI 会话 = 高频全文替换 = Git 冲突地狱。

### 设计方案

#### 总体策略：按用户分文件 + 共享文件只读/append

```
memory-bank/
  activeContext/                    ← 新：目录替代单文件
    _shared.md                      ← 团队共享上下文（只读 or append-only）
    mengzhoyang.md                  ← 个人活跃上下文
    zhangsan.md                     ← 个人活跃上下文
    ...                             ← 每人一个文件，最多 10 个
  progress.md                       ← 保持不变（低频，append 模式）
  techContext.md                    ← 保持不变（低频，append 模式）
  systemPatterns.md                 ← 保持不变（低频，append 模式）
  projectbrief.md                   ← 保持不变（极低频）
```

#### 改动 1：activeContext 从单文件改为用户分区目录

**原理**：每人写自己的文件，Git 合并零冲突。

| 操作 | 旧行为 | 新行为 |
|------|--------|--------|
| 会话开始读取 | `memory_get("memory-bank/activeContext.md")` | `memory_get("memory-bank/activeContext/{user}.md")` + `memory_get("memory-bank/activeContext/_shared.md")` |
| 会话结束写入 | `memory_write("memory-bank/activeContext.md", ..., mode="overwrite")` | `memory_write("memory-bank/activeContext/{user}.md", ..., mode="overwrite")` |
| 团队公告/决策 | 无 | `memory_write("memory-bank/activeContext/_shared.md", ..., mode="append")` |

**`{user}` 来源**：`get_current_user(config.repo_root)` — 已有实现，优先读 `.vscode/settings.json["memory-mcp.userName"]`。

**实现要点**：
- `memory_writer.py`：新增 `resolve_user_path(path, user)` 函数
  - 当 path 匹配 `memory-bank/activeContext.md` 时，自动重定向到 `memory-bank/activeContext/{user}.md`
  - 其他路径不受影响
- `memory_reader.py`：`memory_get` 同理，读取时自动重定向
- `server.py`：工具描述更新，说明 activeContext 的用户分区行为
- 向后兼容：如果旧的 `activeContext.md` 单文件仍存在，首次启动时自动迁移到 `activeContext/{user}.md`

#### 改动 2：overwrite 模式注入用户标签尾注

**原理**：即使是个人文件，也需要可追溯性。

```python
# memory_writer.py — overwrite 分支
else:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    footer = f"\n<!-- last overwritten by {current_user} at {timestamp} -->\n"
    final_content = content.rstrip("\n") + "\n" + footer
```

**影响**：所有 overwrite 写入的文件末尾都会有 `<!-- last overwritten by xxx at xxx -->` 标签。

#### 改动 3：共享文件强制 append + 用户标签

**原理**：`progress.md`、`techContext.md`、`systemPatterns.md` 是团队共享知识，多人写入应使用 append 模式。

**实现**：
- `config.json` 的 guard targets 新增 `write_policy` 字段：

```json
{
  "path": "memory-bank/progress.md",
  "write_policy": "append_only",
  "role": "feature completion status, milestones"
}
```

- `memory_writer.py`：写入前检查 target 的 `write_policy`
  - `"append_only"`：如果调用方传入 `mode="overwrite"`，自动降级为 `append` 并在返回中标注 `"policy_override": "append_only"`
  - `"user_scoped"`：触发改动 1 的用户分区逻辑
  - `null` / 不设置：保持当前行为

#### 改动 4：`.ai-context/` 用户隔离（共享开发机场景）

**原理**：`.ai-context/` 不进 Git，但同一机器多人共用时会互相覆盖。

```
.ai-context/
  mengzhoyang/
    current-task.md
    latest-error.md
  zhangsan/
    current-task.md
    latest-error.md
```

**实现**：与改动 1 类似，`resolve_user_path` 对 `.ai-context/` 下的路径也做用户分区。

**注意**：这个改动只在共享开发机场景下有意义。如果每人独立机器，`.ai-context/` 天然隔离，此改动可延后。

#### 改动 5：全局预算扩容

| 参数 | 当前值 | 10 人建议值 | 理由 |
|------|--------|-------------|------|
| `total_max_chars` | 60,000 | 150,000 | 10 人 × 8K activeContext + 共享文件 |
| `total_max_tokens` | 15,000 | 40,000 | 同比例扩容 |
| `guard_default_max_chars` | 12,000 | 12,000 | 单文件上限不变 |
| 每人 activeContext 上限 | — | 6,000 chars | 新增 per-user guard target |

#### 改动 6：config.json 新增结构

```json
{
  "multi_user": {
    "enabled": true,
    "user_scoped_paths": [
      "memory-bank/activeContext.md",
      ".ai-context/current-task.md",
      ".ai-context/latest-error.md"
    ],
    "shared_paths_policy": {
      "memory-bank/progress.md": "append_only",
      "memory-bank/techContext.md": "append_only",
      "memory-bank/systemPatterns.md": "append_only",
      "memory-bank/projectbrief.md": "append_only"
    }
  },
  "guard": {
    "total_max_chars": 150000,
    "total_max_tokens": 40000,
    "per_user_max_chars": 6000,
    "targets": [
      {
        "path": "memory-bank/activeContext/{user}.md",
        "max_chars": 6000,
        "write_policy": "user_scoped",
        "role": "per-user sprint focus and recent decisions"
      },
      {
        "path": "memory-bank/activeContext/_shared.md",
        "max_chars": 8000,
        "write_policy": "append_only",
        "role": "team-wide announcements, shared decisions"
      }
    ]
  }
}
```

### 迁移计划

```
Phase A — 基础设施（无破坏性变更）
  1. memory_writer.py: 实现 overwrite 尾注（改动 2）
  2. memory_config.py: 新增 multi_user / write_policy 配置解析
  3. memory_writer.py: 实现 write_policy 检查（改动 3）
  4. 测试：全部原有测试通过 + 新增 write_policy 测试

Phase B — 用户分区（核心改动）
  5. memory_writer.py + memory_reader.py: 实现 resolve_user_path（改动 1）
  6. server.py: 工具描述更新
  7. config.json: 新增 multi_user 配置（改动 6）
  8. 迁移脚本: activeContext.md → activeContext/{user}.md
  9. 测试：用户分区读写 + 迁移 + 向后兼容

Phase C — 扩容与可选改动
  10. config.json: 全局预算扩容（改动 5）
  11. .ai-context 用户隔离（改动 4，可选，视是否有共享开发机需求）
  12. copilot-instructions.md: 更新 Memory Bank 硬规则中的路径说明
```

### Git 冲突分析（改动后）

| 文件 | 写入频率 | 写入模式 | 冲突概率 |
|------|----------|----------|----------|
| `activeContext/{user}.md` | 高（每次会话） | overwrite | **零**（每人独立文件） |
| `activeContext/_shared.md` | 低（团队决策时） | append | **极低**（append 天然可合并） |
| `progress.md` | 低 | append | **极低** |
| `techContext.md` | 低 | append | **极低** |
| `systemPatterns.md` | 低 | append | **极低** |
| `projectbrief.md` | 极低 | append | **几乎为零** |

### 风险与注意事项

1. **AI 指令适配**：`copilot-instructions.md` 中的 Memory Bank 硬规则需要同步更新路径（`activeContext.md` → `activeContext/{user}.md`），否则 AI 会话仍会尝试写入旧路径
2. **向后兼容**：需要处理旧的 `activeContext.md` 单文件到新目录结构的迁移，建议首次检测到旧文件时自动迁移
3. **用户名一致性**：10 人团队必须确保每人的 `.vscode/settings.json` 中配置了唯一的 `memory-mcp.userName`，否则会出现用户名冲突。建议在 `memory_writer.py` 中增加用户名校验（非空、非 unknown）
4. **compaction 适配**：`memory_compact` 的 `warm_context` 策略需要适配目录结构，对每个用户文件独立执行 compaction
5. **memory_search 适配**：搜索范围需要包含 `activeContext/` 目录下的所有用户文件
6. **guard 适配**：per-user guard target 需要动态匹配 `{user}` 占位符

### 工作量估算

| Phase | 改动文件数 | 预估工时 | 优先级 |
|-------|-----------|----------|--------|
| A（基础设施） | 2-3 | 2h | 高 |
| B（用户分区） | 4-5 | 4h | 高 |
| C（扩容与可选） | 2-3 | 2h | 中 |
| 测试 | 1-2 | 2h | 高 |
| 文档更新 | 2 | 1h | 中 |
| **合计** | | **~11h** | |

---

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
