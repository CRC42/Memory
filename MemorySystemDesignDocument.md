# MCP 记忆系统设计文档

> 状态：vNext 设计稿；P0/P1/P2 与 v0.4.1 P0 加固已落地
>
> 日期：2026-04-21
>
> 适用范围：`ToolTest/MCP/Memory` 后续演进设计、多人协作治理、MCP 对外接口扩展

## 0. 当前项目基线

本设计文档不是脱离现状的重写方案，而是建立在当前 `MCP/Memory` 已有实现之上的下一阶段演进方案。

### 0.1 已有实现

当前仓库中的 Memory MCP 已具备以下基础能力：

- `memory_get`：读取记忆文件内容，支持截断和多人模式重定向
- `memory_search`：关键词检索，支持 CJK 文本
- `memory_guard_check`：记忆容量与预算检查
- `memory_backup`：记忆文件备份与轮转
- `memory_compact`：基于规则的压缩与摘要
- `memory_write`：受控写入，支持原子写入、备份、策略降级和用户标签注入

### 0.2 当前存储分层

当前项目已形成三层记忆根目录：

- `memory-bank/`：进 Git 的共享项目记忆
- `.ai-context/`：不进 Git 的本地临时上下文
- `.ai-memory/`：配置、备份、事件、缓存等基础设施

其中现有 `memory-bank/` 主要包含：

- `activeContext.md` / `activeContext/{user}.md`：当前焦点与近期决策
- `progress.md`：进度与里程碑
- `techContext.md`：技术上下文
- `systemPatterns.md`：系统模式与约定

### 0.3 当前能力边界

当前版本已经解决了“多人协作下的高频写入冲突”和“基础读写、检索、压缩、预算保护”问题，但仍存在以下演进空间：

- 正式记忆记录缺少统一的结构化外壳
- 系统记忆与个人记忆还没有完整的治理与发布流
- 检索层仍以文件粒度为主，尚未形成统一记录索引
- 缺少候选池、归档层、编译产物层的正式目录模型
- MCP 对外接口仍偏文件操作，尚未提升到“记录级”和“发布级”能力

因此，本文档定义的目标不是替换当前系统，而是在兼容现有 Memory MCP 的前提下，为后续开发提供统一的架构设计。

## 1. 文档目标

构建一套适用于多人协作的项目记忆系统，满足以下目标：

- 支持个人长期沉淀与协作交接
- 支持系统共识治理与正式发布
- 支持全文检索与历史追溯
- 支持与 Git 工作流兼容
- 支持以 MCP 形式对外提供统一接口
- 在无 LLM 条件下仍可运行
- 在有 LLM 条件下支持更强的分类、提炼、编译能力

## 2. 核心设计原则

### 2.1 真源原则

记忆系统的真源不是模型输出，而是：

- Markdown 记忆文件
- 结构化元数据
- 事件日志
- 候选记录
- 已发布系统记忆

LLM 只能参与提炼和填表，不能成为唯一真源。

### 2.2 分层原则

记忆必须分层管理：

- 个人记忆：个人持续沉淀
- 系统记忆：少量高价值团队共识
- 历史记忆：归档与长期可检索记录
- 本地临时记忆：当前会话与调试信息
- 编译记忆：面向运行时生成的派生产物

### 2.3 编译原则

“记忆编译”不是让 LLM 再写一遍总结，而是：

- 从结构化记录中筛选
- 按规则路由和聚合
- 生成当前运行时所需视图

编译阶段以确定性逻辑为主，LLM 仅作增强。

### 2.4 治理原则

系统记忆不是自由编辑的大文档，而是经过治理的知识层：

- 写入候选
- 校验来源、冲突、重复、作用域
- 审核通过后发布
- 长期不用可降级或归档

### 2.5 降级原则

整个系统必须支持三档运行模式：

- 无 LLM：基础能力完整可用
- 本地小模型：轻量增强
- 云端 LLM：高质量提炼与总结

任何情况下，LLM 不可用都不能阻断基础记忆写入、检索和编译。

## 3. 系统边界

### 3.1 本系统负责

- 记忆写入
- 结构化记录管理
- 标签与分类管理
- 候选验证与发布
- 编译生成运行时记忆视图
- 检索与索引
- Git 兼容存储
- 通过 MCP 暴露能力

### 3.2 本系统不负责

- 直接替代项目文档系统
- 直接替代 issue 管理系统
- 直接替代代码仓库
- 让 LLM 自动决定正式系统共识
- 让 LLM 直接自动发布正式 skill

## 4. 记忆分层模型

### 4.1 个人记忆

用途：

- 个人负责事项
- 阶段结论
- 踩坑经验
- 交接说明
- 暂未验证但值得保留的信息

特点：

- 每人独立维护
- 可以进 Git
- 是正式真源之一
- 允许逐步沉淀

### 4.2 系统记忆

用途：

- 已验证规则
- 团队必须知道的边界
- 高复用模式
- 跨人协作关键共识

特点：

- 数量必须少
- 内容必须稳定
- 不允许所有人直接自由改写
- 必须经过 candidate -> validated -> published

### 4.3 历史记忆

用途：

- 归档后的旧结论
- 候选池历史
- 事件日志
- 老版本记忆
- 长期全文检索数据

特点：

- 不默认注入上下文
- 可检索
- 可追溯
- 可重新编译

### 4.4 本地临时记忆

用途：

- 当前会话缓存
- 临时上下文
- 调试信息
- 快速工作草稿

特点：

- 不进 Git
- 不作为正式真源
- 生命周期短

### 4.5 编译记忆

用途：

- 运行时注入上下文
- 当前任务 handoff
- 当前用户 digest
- 当前分支 digest
- 系统 digest
- 候选发布视图

特点：

- 由系统编译生成
- 不是手工主编辑层
- 可重建
- 可丢弃

## 5. 目录结构

### 5.1 vNext 目标目录

```text
memory-bank/
  shared/                       # 已发布系统记忆
  people/
    {user}/                     # 个人记忆
  candidates/                   # 系统记忆 / skill 候选池
  archive/                      # 降级与归档层
  compiled/                     # 编译产物
    runtime/
      system-digest.md
      people/
        {user}-digest.md
      task/
        {task_id}.md
      branch/
        {branch}.md
    publish/
      system-candidates.jsonl
      skill-candidates.jsonl

.ai-context/
  {user}/                       # 本地临时记忆，不进 Git

.ai-memory/
  config.json                   # 配置
  search.db                     # SQLite FTS 派生索引，不进 Git
  events.jsonl                  # 事件日志，不进 Git
  compile-cache/                # 编译缓存，不进 Git
  temp/                         # 临时文件，不进 Git
  backups/                      # 备份，不进 Git
```

### 5.2 与当前项目的关系

为兼容当前仓库已经使用的 `activeContext.md`、`progress.md`、`techContext.md`、`systemPatterns.md`，建议分阶段迁移：

- 第一阶段保留现有文件接口不变
- 新增记录层、候选层、归档层、编译层目录
- 通过编译过程继续生成现有的人类可读摘要文件
- 待调用方稳定迁移后，再逐步把旧文件视为编译视图而非主真源

## 6. 存储格式设计

### 6.1 总体原则

- 传输格式：JSON
- 正文格式：Markdown
- 正式落盘格式：Markdown + YAML Front Matter
- 索引格式：SQLite FTS 派生索引
- 事件流格式：JSONL

也就是说：

- MCP 调用时传 JSON
- 内容正文用 Markdown
- 标签和关键元数据放在 Front Matter
- 检索层通过解析 Front Matter 和正文建立索引

### 6.2 记忆记录的统一外壳

所有正式记忆记录共享一套基础字段：

```yaml
schema_version: "1.0"
id: mem_20260421_001
record_kind: rule_candidate
scope: personal
status: candidate
author: yangskin
created_at: 2026-04-21T10:30:00+08:00
updated_at: 2026-04-21T10:30:00+08:00
tags:
  - asset_pipeline
  - texture
  - validation
confidence: 0.82
source_refs:
  - evt_1023
  - note_88#chunk_3
task_id: task_sp_sync
branch: feature/sp-roundtrip
validated_by: null
last_used_at: null
classifier_model: gpt-5
classifier_prompt_version: "v1"
tag_schema_version: "v1"
```

### 6.3 Markdown 记忆文件示例

```markdown
---
schema_version: "1.0"
id: mem_20260421_001
record_kind: rule_candidate
scope: personal
status: candidate
author: yangskin
created_at: 2026-04-21T10:30:00+08:00
updated_at: 2026-04-21T10:30:00+08:00
tags:
  - asset_pipeline
  - texture
  - validation
confidence: 0.82
source_refs:
  - evt_1023
  - note_88#chunk_3
task_id: task_sp_sync
branch: feature/sp-roundtrip
validated_by: null
last_used_at: null
classifier_model: gpt-5
classifier_prompt_version: "v1"
tag_schema_version: "v1"
---

# 导出链路尺寸约束

导出链路必须保留 `max_texture_size` 约束。

## 依据

- 配置文件中已存在该字段
- 测试中发现忽略该字段会导致导出结果异常

## 备注

当前结论仍需进一步验证 grayscale filter 的边界情况。
```

### 6.4 记录类型

建议初始只支持少量封闭类型：

- `note`：普通笔记
- `event`：事件记录
- `claim_candidate`：结论候选
- `rule_candidate`：规则候选
- `handoff`：交接记录
- `skill_candidate`：流程候选
- `validation_result`：验证结果
- `system_rule`：正式系统规则
- `archive_record`：归档记录

注意：`record_kind` 是记录本体类型，不能和 `tags` 混用。

### 6.5 标签设计

标签只用于辅助组织、路由、编译和检索，不承担最终事实判定。

建议分为：

主题标签，例如：

- `asset_pipeline`
- `texture`
- `material`
- `validation`
- `build`
- `workflow`
- `ui`
- `mcp`

作用提示标签，例如：

- `high_value`
- `needs_validation`
- `handoff_ready`
- `archive_candidate`
- `skill_possible`

严格要求：

- 初期标签数量要少
- 标签必须是受控词表，不允许自由发明
- 标签可多选
- 标签允许后续修正与重分类

## 7. 写入模型

### 7.1 写入原则

写入阶段分两部分：

硬元数据，由系统确定或显式传入，不依赖 LLM：

- `author`
- `created_at`
- `source`
- `task_id`
- `branch`
- `workspace`
- `event_id`
- `status` 默认值
- `file path`

软元数据，可由当前调用的 LLM 在 MCP 请求时填写：

- `record_kind`
- `tags`
- `confidence`
- `scope_hint`
- `skill_possible`
- `needs_validation`

LLM 在这里的角色是按 schema 填表的记录助手，不是最终裁决者。

### 7.2 写入阶段工作流

路径 A：无 LLM

- 用户或系统提交基础字段
- 系统补齐默认元数据
- 写入 Markdown 记录或事件日志
- 进入候选池或个人记忆
- 后续由规则和人工处理

路径 B：有 LLM

- 当前 LLM 在调用 MCP 前，根据受控 schema 填写记录字段
- MCP 服务校验字段合法性
- 保存为 Markdown + Front Matter
- 触发编译或进入候选验证队列

### 7.3 写入阶段不能做的事

- LLM 直接发布正式系统记忆
- LLM 直接决定最终 skill 发布
- LLM 直接删除正式系统记忆
- LLM 在无来源时生成系统共识

## 8. 编译模型

### 8.1 编译定义

编译不是重新写总结，而是：

- 从结构化记录中筛选
- 按规则分组和聚合
- 生成当前目标视图

即：

`原始记录 -> 受控编译 -> 运行时视图`

### 8.2 编译输入

编译器可使用以下信息：

- `record_kind`
- `scope`
- `status`
- `tags`
- `source_refs`
- `confidence`
- `freshness`
- `author`
- `task_id`
- `branch`
- `validated_by`
- `last_used_at`
- 正文 Markdown

其中：

- 前面的结构化字段用于主路由
- 正文只用于补充与必要提取
- 不能只靠标签做最终判断

### 8.3 编译输出

初始建议支持以下编译目标：

`runtime digest`

供当前 agent 运行时注入：

- 当前系统规则摘要
- 当前用户相关高价值记忆
- 当前任务关键状态
- 当前分支注意事项

当前首版实现状态：

- 已实现 `memory_compile(target="runtime_digest")`
- 编译产物写入 `memory-bank/compiled/runtime/`
- 默认只包含 `validated` / `published` 记录
- 支持按 `user`、`task_id`、`branch`、`include_scopes`、`include_statuses`、`preferred_tags` 过滤
- 输出为确定性 Markdown，可删除后重建，不作为唯一真源

`task handoff`

供交接或跨会话继续工作：

- 当前状态
- 已确认结论
- 未解决问题
- 下一步动作
- 来源引用

当前首版实现状态：

- 已实现 `memory_compile(target="task_handoff")`
- 产物写入 `memory-bank/compiled/runtime/task/{task_id}-handoff.md`
- 当前为无 LLM 模板式视图，后续可在治理层稳定后增强分类和章节提取

`system digest`

供团队共享：

- 生效规则
- 最近更新
- 冲突项
- 待验证项

当前首版实现状态：

- 已实现 `memory_compile(target="system_digest")`
- 默认读取 shared + published 记录
- 输出 `memory-bank/compiled/runtime/system-digest.md`
- 当前为确定性模板输出，后续可增加冲突检测和健康检查

`publish queue`

供治理流程使用：

- 待审 system 候选
- 待审 skill 候选

当前首版实现状态：

- 已实现 `memory_compile(target="publish_queue")`
- 默认读取 candidate 记录
- 输出 `memory-bank/compiled/publish/publish-queue.md`
- 可用于审核前查看 system / skill 候选池

### 8.4 编译原则

原则 1：标签只做路由，不做真相。

标签可以决定“看什么、先看什么、送去哪个编译目标”，但不能单独决定“什么是真正系统记忆”。

原则 2：优先读结构，再读正文。

先看：

- `kind`
- `scope`
- `status`
- `tags`
- `confidence`
- `source_refs`

必要时再读正文。

原则 3：编译结果可重建。

编译产物不得成为唯一真源。任何时候都应可由原始记录重新生成。

原则 4：编译支持增量。

只重编译受影响范围：

- 当前任务
- 当前分支
- 当前用户
- 当前候选池
- 当前系统 digest

## 9. 检索模型

### 9.1 检索目标

支持统一检索以下层：

- 系统记忆
- 当前用户个人记忆
- 其他个人记忆
- 候选池
- 归档层
- 事件日志

默认范围：

- 系统记忆 + 当前用户个人记忆

按需扩展到更大范围。

### 9.2 检索实现

真源：

- Markdown
- JSONL
- Front Matter 元数据

派生索引：

- SQLite FTS
- 中文检索采用无新增依赖的 CJK bigram/trigram 派生 token 作为基础兜底，避免第一阶段引入分词库和离线包维护成本

检索流程：

- 解析 Front Matter
- 建立元数据索引
- 建立正文与 metadata 的 FTS 索引
- 为中文标题和正文生成 bigram/trigram 搜索文本
- 检索时先做作用域过滤
- 再做 metadata/tag 过滤
- 最后做全文检索与排序

### 9.3 检索策略

默认优先级：

- 已发布系统记忆
- 当前用户个人记忆
- 当前任务/分支相关记录
- 候选与归档
- 全量历史

回退策略：

如果标签或分类缺失，系统必须仍可通过以下条件完成兜底检索：

- `scope`
- `author`
- 时间范围
- 关键词
- 全文检索

## 10. 验证与发布治理

### 10.1 候选流程

系统记忆与 skill 必须经过：

`raw -> candidate -> validated -> published -> degraded/archive`

`candidate`

- 初步候选
- 允许来自个人记忆或 LLM 提炼

`validated`

- 来源完整
- 无明显冲突
- 作用域合理
- 通过规则校验
- 必要时通过 owner 审核

当前实现：

- `memory_validate_candidate` 将 candidate/raw 记录标记为 `validated`
- 写入 `validated_by`
- 将记录从 `memory-bank/candidates/` 移入对应治理层
- 可通过配置限制 reviewer
- 可校验 `source_refs`、`confidence`、重复记录

`published`

- 正式进入 `shared/` 或 skill 正式层

当前实现：

- `memory_publish_candidate` 只允许发布 `validated` 且带 `validated_by` 的记录
- 发布后记录进入 `memory-bank/shared/{id}.md`
- 若原类型为 `*_candidate`，发布后转为 `system_rule`
- 可通过配置限制 publish owner
- 发布前检查与现有 shared published system rule 的标题冲突

`degraded/archive`

- 长期未使用
- 失效
- 被 supersede
- 降级归档

当前实现：

- `memory_archive_record` 将记录移入 `memory-bank/archive/{id}.md`
- 写入 `archive_reason` / `archived_at`
- `memory_delete_record` 只允许删除 archived 记录，并写入 `.ai-memory/tombstones.jsonl`

### 10.4 维护与健康检查

当前实现：

- `memory_health_check`：检查 metadata 缺失、未知 tag、未闭合 Front Matter、缺失 `search.db`
- `memory_migrate_records`：迁移 `schema_version` 并写入 `schema_migrated_from`
- `memory_update_index`：对指定记录路径增量更新 SQLite FTS，并拒绝非 `list[str]` 的路径参数
- 编译时记录 `last_used_at`（v0.4.1 起改为写入 `.ai-memory/usage-stats.json`，不再回写源记录的 Front Matter，保持源记录可重建、Git diff 干净；如需读取请通过 `get_record_last_used_at(config, record_id)`）
- 编译时写入 `.ai-memory/compile-cache/` manifest
- 编译参数执行显式类型校验，`include_scopes` / `include_statuses` / `preferred_tags` 必须为 `list[str]`
- 配置化 tag schema 通过显式参数传递，避免多配置或多人场景下的隐式全局状态串扰

### 10.5 安全与原子性保证（v0.4.1 加固）

- 记录写入：`memory_write_record` 用 `os.open(..., O_CREAT | O_EXCL | O_WRONLY)` 创建文件，关闭 already-exists 检查与写入之间的 TOCTOU 窗口；并发同 id 写入由 OS 仲裁，败者收到 `already_exists`。
- 状态迁移：`memory_validate_candidate` / `memory_publish_candidate` / `memory_archive_record` 全部走 "临时文件 → `os.replace` → 删除旧路径" 顺序，任意一步失败都不会出现两份或丢失记录。
- 检索查询：`memory_search_records` 对用户输入用 `build_fts5_match_query()` 生成全 phrase 包裹的 MATCH 表达式，避免 `-` / `OR` / `:` / 引号触发 SQLite FTS5 语法错误。
- 普通写入：`memory_write` 的用户尾注按文件扩展名自动判断（仅 `.md` / `.markdown`），可通过 `inject_user_tag` 强制开启或关闭，避免破坏 JSON / YAML / 源码。
- 多人迁移：`resolve_user_path` 在第一次自动迁移共享文件到 `{user}.md` 时，会在文件顶部插入 `migrated-from-shared` banner，提示作者归属未经验证。
- 审计日志：`events.jsonl` 超过阈值（默认 5 MB，环境变量 `MEMORY_MCP_EVENTS_MAX_BYTES`）会按时间戳归档，仅保留最近 N 份（默认 5，环境变量 `MEMORY_MCP_EVENTS_MAX_ARCHIVES`）。

### 10.2 验证规则

至少包括：

- 必填字段检查
- `source_refs` 是否存在
- `scope` 是否合法
- 是否越权写入 `shared`
- 是否与已有记录重复
- 是否与已有系统规则冲突
- 是否缺少 evidence
- `confidence` 是否过低
- 是否误把个人经验升系统层
- 是否引用了已失效来源

### 10.3 正式发布权

允许自动处理：

- 个人记忆写入
- 本地临时记忆写入
- 候选创建
- 归档建议
- 编译视图生成

不允许自动最终决定：

- 系统记忆正式发布
- skill 正式发布
- 正式系统记忆删除
- 高风险冲突裁决

## 11. MCP 接口设计

### 11.1 基础能力接口（无 LLM 也可运行）

- `memory_write_record`
- `memory_append_event`
- `memory_search`
- `memory_compile`
- `memory_validate_candidate`
- `memory_publish_candidate`
- `memory_archive_record`
- `memory_rebuild_index`
- `memory_get_runtime_digest`

### 11.2 增强能力接口（可选依赖 LLM）

- `memory_classify_record`
- `memory_extract_candidate`
- `memory_merge_candidates`
- `memory_generate_skill_candidate`
- `memory_explain_conflict`
- `memory_generate_handoff`

这些接口失败时不得影响基础链路。

### 11.3 MCP 写入接口示例

```json
{
  "record_kind": "rule_candidate",
  "scope": "personal",
  "status": "candidate",
  "author": "yangskin",
  "tags": ["asset_pipeline", "texture", "validation"],
  "confidence": 0.82,
  "source_refs": ["evt_1023", "note_88#chunk_3"],
  "task_id": "task_sp_sync",
  "branch": "feature/sp-roundtrip",
  "content_markdown": "# 导出链路尺寸约束\n\n导出链路必须保留 `max_texture_size` 约束。\n\n## 依据\n\n- 配置文件中已存在该字段\n- 测试中发现忽略该字段会导致导出结果异常\n"
}
```

### 11.4 MCP 编译接口示例

```json
{
  "target": "runtime_digest",
  "user": "yangskin",
  "task_id": "task_sp_sync",
  "branch": "feature/sp-roundtrip",
  "include_scopes": ["shared", "personal"],
  "include_statuses": ["validated", "published"],
  "preferred_tags": ["asset_pipeline", "texture"]
}
```

## 12. 无 LLM 兜底方案

### 12.1 必须保证的能力

在无 LLM 条件下，系统仍必须支持：

- 原始记录写入
- Markdown 存储
- Front Matter 解析
- 事件日志记录
- 标签缺省写入
- 规则校验
- SQLite FTS 检索
- 模板式 digest 编译
- 候选验证与发布流程
- Git 协作

### 12.2 无 LLM 时的编译策略

采用模板和规则生成：

- 当前系统规则列表
- 当前用户最近高价值记录
- 当前任务最新状态
- 待验证项
- 当前 handoff 草稿

也就是说，没有 LLM 时，也要能得到“稳但朴素”的编译结果。

## 13. Git 策略

### 13.1 进入 Git

- `memory-bank/shared/`
- `memory-bank/people/{user}/`
- `memory-bank/candidates/`
- `.ai-memory/config.json`

### 13.2 不进入 Git

- `.ai-context/`
- `.ai-memory/search.db`
- `.ai-memory/events.jsonl`
- `.ai-memory/backups/`
- `.ai-memory/temp/`
- `.ai-memory/compile-cache/`

## 14. 权限与冲突策略

### 14.1 多人协作原则

- 个人增量提交为主
- 不鼓励多人直接共写同一系统正文文件
- 系统记忆通过候选汇总与发布
- 共享内容尽量采用追加、分段、生成式汇总

### 14.2 冲突规避

- 优先单人维护个人目录
- `shared` 层尽量由发布流程写入
- 编译产物可重建，不作为人工主编辑层
- 用候选池承接多人输入

## 15. 推荐实现阶段

### 阶段 1：基础版

目标：

- Markdown + Front Matter 规范
- 基础写入接口
- SQLite FTS 检索
- 基础编译视图
- 无 LLM 兜底链路

### 阶段 2：结构化增强版

目标：

- LLM 按 schema 填表
- 标签受控词表
- 候选验证规则
- `runtime digest` / `task handoff` 编译
- 编译缓存与增量更新

### 阶段 3：治理发布版

目标：

- system candidate 验证
- skill candidate 验证
- 冲突检测
- 发布与降级流程
- owner 审核机制

### 阶段 4：高级增强版

目标：

- 本地小模型支持
- 云端 LLM 批处理提炼
- 重分类与 schema 升级
- 周期性 lint
- 系统记忆健康检查

优先级说明：

- LLM 增强优先于 RAG / 向量检索。
- LLM 只作为分类、提炼、候选生成、冲突解释和摘要增强器。
- LLM 不得直接发布系统记忆，不得绕过治理流程。
- 无 LLM 时，基础写入、FTS 检索、编译和治理必须继续可用。

### 阶段 5：本地 RAG / 向量召回增强版

目标：

- 在 LLM 增强稳定后，再评估本地 embedding / 向量召回。
- 只用于语义模糊召回、相似记录推荐、候选去重建议和冲突候选提示。
- 向量索引放在 `.ai-memory/` 下，作为可删除、可重建的派生产物。
- RAG 不参与真源判定，不参与权限判断，不直接决定正式发布。
- 推荐检索顺序为：metadata 过滤 -> SQLite FTS -> 向量补召回 -> 规则或 LLM 重排 -> 返回带 record id / path 的结果。

### 15.5 与当前仓库的落地顺序建议

结合 `ToolTest/MCP/Memory` 当前已落地的 v0.4.0 能力，建议按以下顺序推进：

1. 先在现有 `memory_write` / `memory_search` / `memory_guard_check` 基础上增加“记录级”抽象，而不是立即推翻现有文件级接口。
2. 优先补齐 `Front Matter` 解析、记录落盘规则、SQLite FTS 派生索引，形成真正的记录模型。
3. 以“兼容现有 `memory-bank/*.md` 文件”为前提实现 `runtime digest` 和 `task handoff` 编译，避免一次性迁移所有调用方。
4. 等记录层稳定后，再引入候选验证、发布、归档和 skill 候选治理。
5. 之后接入本地模型或云端 LLM，把 LLM 能力限制在分类、提炼、冲突解释和候选生成等增强位。
6. 最后再评估本地 RAG / 向量召回；RAG 的优先级低于 LLM 增强，且只能作为语义召回补充。

## 16. 最终结论

本方案最终采用以下总体架构：

形式：

- MCP 提供记忆能力接口
- JSON 用于接口传输
- Markdown + Front Matter 作为正式存储格式
- SQLite FTS 作为派生检索层

写入：

- 当前 LLM 可按 schema 填写结构化记录
- 标签与分类可在写入阶段前置生成
- 但正式系统记忆不能由 LLM 直接决定

编译：

- 编译阶段以确定性规则为主
- 标签用于路由和聚合，不作为唯一真相依据
- 编译产物是运行时视图，不是唯一真源

治理：

- 个人记忆持续沉淀
- 系统记忆严格治理
- `候选 -> 验证 -> 发布`
- 长期未使用内容可降级归档

兜底：

- 无 LLM 时系统仍可完整运行
- 有 LLM 时只增强质量，不改变基础依赖关系

### 16.1 对当前项目的直接意义

对 `ToolTest/MCP/Memory` 而言，这份设计文档的作用不是替代当前 README，而是补齐“当前版本为何存在、下一阶段要长成什么样、迁移过程中如何兼容现有调用方式”这三件事。后续如果继续开发 Memory MCP，应以本文作为 vNext 架构基线，并把 README 继续作为当前实现说明维护。
