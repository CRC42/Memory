# MCP 记忆系统设计文档

> 状态：vNext 设计稿；P0/P1/P2、P3 结构升级首版与 P3 hardening 已落地
>
> 日期：2026-04-23
>
> 适用范围：`ToolTest/MCP/Memory` 后续演进设计、重要记忆输出、MCP 对外接口扩展

## 0. 当前项目基线

本设计文档不是脱离现状的重写方案，而是建立在当前 `MCP/Memory` 已有实现之上的下一阶段演进方案。

### 0.1 已有实现

当前仓库中的 Memory MCP 默认对外暴露 4 个 facade tools，并保留 legacy/admin 兼容工具：

- `memory_read`：读取文件、普通搜索、结构化记录搜索、读取 runtime digest
- `memory_write`：普通文件写入、结构化记录写入、observation 证据写入、artifact/facet 关联（可选 distill）
- `memory_context`：编译 runtime / snapshot / review 视图、读取 digest、追踪 lineage、列出 conflicts、对比 snapshot、装配上下文（可选 summarize）
- `memory_enhance`（6 个 opt-in LLM 增强能力的 read-only facade）
- `mcp.expose_admin_tools=true`：开发期或纯 MCP 客户端兼容完整 legacy/admin 工具集，合并后去重总数 24（4 facade + 20 legacy/admin）

内部已具备：路径安全、原子写入、备份/压缩/guard、CJK 搜索、结构化记录、SQLite FTS、治理发布、确定性编译、P3 snapshot/scoring/retrieval、record IO 公共层和深度健壮性加固。

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

当前版本已经解决了“默认开启多人协作安全策略”“多人协作下的高频写入冲突”“基础读写、检索、压缩、预算保护”“结构化记录治理”“P3 证据驱动上下文装配”和“深度健壮性边界”问题，但仍存在以下演进空间：

- 内部实现仍有大文件：`memory_compiler.py` 已完成 render / targets / scoring 首轮拆分，但 snapshot / review / rollback 编译逻辑仍可继续拆；`server.py` 也可继续拆分
- `memory_retrieval` 已切到 `memory_corpus.CompilableRecord` / `compact_body`，不再依赖 compiler 私有符号；后续可继续把 corpus 与 compiler 之间的接口稳定化
- Front Matter parser/dumper 仍在 `memory_records.py`，后续应抽 `memory_frontmatter.py`
- `memory_retrieval` 已升级为严格 `budget-first`：`retrieve_context` 与 `important_memories` 共用预算打包原语，并返回 `context_items` / `budget_report` / `dropped_candidates` / `evidence_refs`
- `memory_context(operation="important_memories")` 已上线，提供小预算重要记忆输出，外部规则沉淀流程可直接消费
- 当前文档仍把联合治理扩展写成主线，但新的产品方向应是“动态记忆供给”，而不是“插件内自动审查”
- CLI / 管理 skill 需要承接 guard、backup、compact、index、governance、snapshot review 等低频管理动作

因此，本文档定义的目标不是替换当前系统，而是在当前 3-tool facade 与 P3 完成态之上，把后续主线明确收敛为：以小预算输出重要记忆，供用户或外部 agent/规则系统继续沉淀；联合治理能力保留为兼容层，不再作为最高优先级。

当前默认产品定位也随之明确为：**用户不需要做任何维护**。日常使用只涉及记忆写入、普通检索和简洁上下文装配；只有在需要提取“当前重要信息”时，才调用详细的重要记忆输出接口。

## 1. 文档目标

构建一套适用于多人协作的项目记忆系统，满足以下目标：

- 支持个人长期沉淀与协作交接
- 支持输出“小而准”的重要记忆供 LLM / agent 复用
- 支持把值得长期沉淀的记忆导出为外部规则系统的候选输入
- 支持全文检索与历史追溯
- 支持与 Git 工作流兼容
- 支持以 MCP 形式对外提供统一接口
- 在无 LLM 条件下仍可运行
- 在有 LLM 条件下支持更强的分类、提炼、编译能力

## 2. 核心设计原则

### 2.0 自动化原则（首要原则）

> **本系统的目标是尽量"无人值守"。**

记忆落地不应依赖人工确认。LLM 提炼、归类、改写、覆盖、归档全部走自动化链路。
人工只在两个场景介入：

1. 出现明显错误或冲突时手动修正
2. 直接以 `human` 身份新增 raw 原始记忆

为了让"无人值守"安全成立，必须把"原始信息"与"派生信息"严格分层（见 §2.1.A）。

### 2.1.A 真源不可变原则（Raw-Immutable）

**唯一受写保护的层是 `raw` 原始信息层。**

- `raw` 一旦写入即冻结：`immutable=True`、`authoritative=True`、不可被任何
  LLM/agent 修改或删除。源是文件系统 + 事件日志，不是任何 LLM 的输出。
- 任何 LLM 提炼物都属于 `distilled` 派生层：`immutable=False`、`authoritative=False`、
  `status=distilled`，**不需要人工 validate / publish**，可被任意 LLM 自动改写
  或重建。
- 派生记录必须携带 `derived_from=[raw_id, ...]` 字段，把谱系绑死在不可变 raw
  层。这样：
  - 任意 LLM（含本地、不同模型、不同版本）都能从 raw 重新生成 distilled
  - 任意时刻人工都能审阅、回滚、重建任意 distilled 视图
  - 不同 LLM 之间互相覆盖 distilled 不会丢失原始证据

实现层面（参见 `memory_llm.py`）：

- `make_raw_record(...)` — 构造冻结的 raw 记录，标记 `immutable=True`
- `make_distilled_record(..., derived_from=[...])` — 构造可替换的 distilled 记录，
  必须提供 `derived_from`
- `supersede_distilled(previous, ...)` — 用新 LLM 重写 distilled，自动继承
  原 `derived_from` 链路
- `assert_raw_writable(record)` — 写入路径上的硬守卫，遇到 raw 立即抛
  `RawImmutableError`

### 2.1 真源原则

记忆系统的真源不是模型输出，而是：

- Markdown 记忆文件（`raw` 层为主，`distilled` 层为派生快照）
- 结构化元数据
- 事件日志
- 候选记录（兼容历史，新流程默认走 distilled）
- 已发布系统记忆（兼容历史）

LLM 永远不能修改 raw 层；它只能在 distilled 层产出可被自身或其他 LLM 重写的
记录。

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

### 2.4 预算优先原则

上下文装配的第一目标不是“尽可能多返回”，而是“在严格预算内返回最重要的记忆”：

- 先确定 `max_tokens` / `max_chars` / `max_items`
- 再在预算内选择价值最高的记忆块
- 同一条记忆正文不应在多个 section 中重复展开
- 超预算时优先降级渲染，而不是继续扩张返回体

### 2.5 自动化边界原则

本系统的自动化边界是“动态记忆供给”，而不是“插件内自动审查”：

- 自动化负责写 observation / incident / note 等动态记忆
- 自动化负责 scoring、retention、压缩和重要记忆输出
- 自动化可以提示“值得外部沉淀”，但不在插件内维护项目规则真源
- 项目规则由外部系统维护；本插件只提供候选记忆与证据上下文

### 2.6 降级原则

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
- 编译生成运行时记忆视图
- 检索与索引
- 重要记忆评分、保留、压缩与小上下文输出
- 供外部规则系统消费的重要记忆与证据载荷
- Git 兼容存储
- 通过 MCP 暴露能力

### 3.2 本系统不负责

- 直接替代项目文档系统
- 直接替代 issue 管理系统
- 直接替代代码仓库
- 维护项目规则真源
- 让 LLM 自动决定正式系统共识
- 让插件内自动审查成为主流程
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
- 这是当前实现的兼容层；长期稳定项目规则建议由外部系统维护，而不是继续在本插件中扩张治理能力

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

> 在 raw-immutable + distilled-replaceable 模型下，LLM 的写入边界是
> **"raw 永远不能改，distilled 可以随便改"**。

LLM 不能：

- 修改任何 `raw` / `immutable=True` 的记录（必须抛 `RawImmutableError`）
- 删除任何 `raw` 记录或事件日志
- 把 `distilled` 记录提升为 `raw` 或 `authoritative=True`
- 在 `derived_from` 为空时产出 distilled 记录（必须能追溯回 raw）

LLM 可以（无需人工确认）：

- 写入新的 `raw` 记录（`immutable=True` 立即冻结）
- 写入新的 `distilled` 记录
- 用新 LLM 输出 `supersede` 之前的 distilled 记录
- 自动归类、打标、生成 abstract、生成 snapshot narrative

历史 `published` / `validated` / `dao` 层属于兼容路径，仍保留人工/规则限制
（见 §10），但不再是默认链路。

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
- 默认使用 `body_mode="compact"` 输出较短上下文，只保留 `id`、`source`、`status` 和关键段落
- `body_mode="full"` 可显式恢复完整记录正文与详细 metadata 渲染
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
- 当前为无 LLM 模板式 compact 视图，后续可在治理层稳定后增强分类和章节提取

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

原则 5：默认面向运行时上下文压缩。

编译产物默认不机械复制完整记录，而是：

- metadata 用于筛选和路由，不默认逐条展示
- 每条记录保留最小追溯信息：`id`、`source`、`status`
- 正文优先抽取 `Decision`、`Expected Behavior`、`Acceptance Checks`、`Next Step(s)`、`Notes`、`Details`
- 无关键段落时截取正文开头
- 需要审计或调试时可用 `body_mode="full"` 查看完整渲染

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

- 当前任务/分支强相关记录
- 当前用户个人记忆
- 高 importance 的共享/个人记录
- 明确命中的候选与归档
- 全量历史

预算策略：

- 检索入口必须优先接受 `max_tokens` / `max_chars` / `max_items`
- 排序之后不是简单取 `top_k`，而是按预算逐条装配
- 优先保留高价值密度内容；低价值或长正文内容在预算紧张时自动降级
- 返回结果必须包含 `budget_report`，说明已选、已截断、已丢弃的内容

回退策略：

如果标签或分类缺失，系统必须仍可通过以下条件完成兜底检索：

- `scope`
- `author`
- 时间范围
- 关键词
- 全文检索

## 10. 验证与发布治理（兼容层）

### 10.1 候选流程

> ⚠️ **`candidate -> validated -> published` 是历史兼容流程，不是默认路径。**
> 自动化主流程改走 §2.1.A 的 raw-immutable + distilled-replaceable 模型：
> - 新写入默认是 `raw`（冻结）或 `distilled`（可被任意 LLM 覆盖）
> - distilled 不需要人工 validate / publish 即可生效
> - 仅当历史数据迁移、跨团队共识发布等特殊场景才走下面的 candidate 流程

当前仓库仍保留记录验证、发布、归档这条治理链路，用于兼容现有实现、测试和历史数据迁移；但它不再是后续最高优先级。

系统记忆与 skill 当前仍可经过：

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
- 多人模式默认启用：缺省配置下 `memory-bank/activeContext.md` 读写重定向到 `memory-bank/activeContext/{user}.md`；`progress.md` / `techContext.md` / `systemPatterns.md` / `projectbrief.md` 默认 append-only。旧配置即便覆盖了 `guard.targets` 且未写 `write_policy`，也会通过 `multi_user.user_scoped_paths` / `shared_paths_policy` 兜底执行。

### 10.5 安全与原子性保证（v0.4.1 起、v0.5.4–0.5.6 进一步加固）

> 本节概述加固思路；v0.5.x 逐版本落地明细见 §15.8 以及 DEVLOG。

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

### 11.0 对外接口收敛（已完成）与重要记忆输出（新的最高优先级）

当前实现为了验证功能与测试覆盖，曾把内部能力较细粒度地暴露为多个 MCP tools。这个形态适合开发期调试，但不适合作为长期对外接口：AI 客户端会看到过多工具，容易误选高风险管理动作，也会让工具 schema 变得嘈杂。

当前首版已完成：**MCP 对外默认只保留运行时高频读写与上下文能力，管理能力迁移到 CLI / scripts / skill。**

在此基础上，后续最高优先级不再是扩展插件内治理，而是补齐“重要记忆输出”接口：让 LLM / agent 能在严格预算内拿到之前最重要的动态记忆，再由外部流程决定是否沉淀为项目规则。

默认 MCP 对外暴露 4 个 facade tools：

- `memory_read`
- `memory_write`
- `memory_context`
- `memory_enhance`（v0.7.0-P4-B 起；read-only LLM 增强，不写盘）

- `memory_read`
  - 负责读取、搜索、搜索结构化记录、读取 runtime digest。
  - 内部可路由到 `memory_get`、`memory_search`、`memory_search_records`、`memory_get_runtime_digest`。
- `memory_write`
  - 负责普通文件写入、结构化记录写入、observation 证据写入、artifact/facet 关联。
  - 内部可路由到现有 `memory_write`、`memory_write_record`、`memory_record_observation`、`memory_link_artifact`。
- `memory_context`
  - 负责面向当前 AI 会话的上下文装配、编译、重要记忆输出、lineage、conflict、snapshot compare。
  - 内部可路由到 `memory_compile`、`memory_get_runtime_digest`、`memory_trace_lineage`、`memory_list_conflicts`、`memory_compare_snapshots`、`memory_retrieve_context`。

以下能力不再默认暴露为 MCP tools，改为 CLI / scripts / 管理 skill 调用：

- guard / backup / compact
- rebuild index / update index
- health check / schema migrate
- validate / publish / archive / delete
- snapshot rebuild
- conflict review / promote / degrade

推荐最终外观：

```text
MCP:
  memory_read
  memory_write
  memory_context

CLI:
  memory admin guard
  memory admin backup
  memory admin compact
  memory index rebuild/update
  memory govern validate/publish/archive/delete
  memory snapshot daily/weekly/monthly
  memory health
  memory migrate

Skill:
  memory-admin
  memory-governance
  memory-snapshot-review
```

当前兼容策略：

- 内部 Python 函数继续保留，不立即删除。
- 开发期可通过配置开关继续暴露完整工具集：`mcp.expose_admin_tools=true`，当前兼容工具总数为 24（含 4 个 facade）。
- 默认 MCP tool list 收敛为 4 个 facade。
- CLI 和 skill 负责高风险、低频、管理类操作。
- 如果某些纯 MCP 客户端不能执行 CLI，可用配置显式开启 legacy/admin tools。

### 11.1 facade operation 契约（当前 MCP 对外接口）

`memory_read`：

| operation | 内部路由 | 用途 |
|-----------|----------|------|
| `get` | `memory_get` | 读取允许范围内的记忆文件 |
| `search` | `memory_search` | 搜索普通 Markdown 记忆文件 |
| `search_records` | `memory_search_records` | 搜索结构化记录 FTS 索引 |
| `runtime_digest` | `memory_get_runtime_digest` | 读取已编译 runtime digest |

`memory_write`：

| operation | 内部路由 | 用途 |
|-----------|----------|------|
| `file` | `memory_write` | 写普通文件，兼容旧调用 |
| `record` | `memory_write_record` | 写 Markdown + Front Matter 结构化记录 |
| `observation` | `memory_record_observation` | 写 schema v2 observation 证据 |
| `link_artifact` | `memory_link_artifact` | 给现有记录追加 artifact / 工程 facet |

`memory_context`：

| operation | 内部路由 | 用途 |
|-----------|----------|------|
| `compile` | `memory_compile` | 编译 runtime / snapshot / review / rollback / dao-fa-shu 视图 |
| `runtime_digest` | `memory_get_runtime_digest` | 读取已编译 runtime digest |
| `trace_lineage` | `memory_trace_lineage` | 追踪 `derived_from` / `supersedes` / `conflicts_with` |
| `list_conflicts` | `memory_list_conflicts` | 列出 open conflicts 与缺失目标 |
| `compare_snapshots` | `memory_compare_snapshots` | 对比两个 snapshot 的 added / removed / persisted |
| `retrieve_context` | `memory_retrieve_context` | 当前 v1：按 scope / time / facet / recall / rerank 装配简洁上下文 |

下一步计划新增：

| operation | 目标 | 用途 |
|-----------|------|------|
| `important_memories` | 已上线（v0.5.2） | 按 budget-first 输出详细重要记忆、证据引用、丢弃原因和预算报告 |

### 11.2 内部能力（无 LLM 也可运行）

- `memory_write_record`
- `memory_append_event`
- `memory_search`
- `memory_compile`
- `memory_record_observation`
- `memory_link_artifact`
- `memory_trace_lineage`
- `memory_compare_snapshots`
- `memory_retrieve_context`
- `memory_validate_candidate`
- `memory_publish_candidate`
- `memory_archive_record`
- `memory_rebuild_index`
- `memory_get_runtime_digest`

### 11.3 增强能力接口（可选依赖 LLM）

> **v0.7.0-P4-B 起：6 项接口已落地，统一通过单一 facade `memory_enhance` 暴露。**
> 实现位置：`servers/memory_server/memory_llm_enhance.py`；MCP 路由位置：`server_dispatch._dispatch_memory_enhance`。

| 设计名 | facade operation | 实现函数 | 状态 |
|--------|------------------|----------|------|
| `memory_classify_record` | `classify_record` | `classify_record` | ✅ v0.7.0-P4-B |
| `memory_extract_candidate` | `extract_candidates` | `extract_candidates` | ✅ v0.7.0-P4-B |
| `memory_merge_candidates` | `merge_candidates` | `merge_candidates` | ✅ v0.7.0-P4-B |
| `memory_generate_skill_candidate` | `generate_skill_candidate` | `generate_skill_candidate` | ✅ v0.7.0-P4-B |
| `memory_explain_conflict` | `explain_conflict` | `explain_conflict` | ✅ v0.7.0-P4-B |
| `memory_generate_handoff` | `generate_handoff` | `generate_handoff` | ✅ v0.7.0-P4-B |

这些接口失败时不得影响基础链路：

- `memory_enhance` 是**read-only**：所有 op 仅返回结构化建议，**永不写盘**；上层（agent）显式以 `status="candidate"` 调 `memory_write_record` 才会落盘。
- LLM 不可用 / 解析失败 / allowlist 不通过 / 输入非法 → 统一返回 in-band 错误（`llm_unavailable` / `enhance_failed:<op>` / `invalid_input`），绝不破坏基础链路或污染 raw。
- 6 项能力名最终对应 `memory_llm_policy.LLM_CAPABILITY_MATRIX` 注册项；未注册即 `UnknownCapability`。
- 详细字段契约与示例：见 `MCP/Memory/README.md` §3.7。

#### 11.3.1 LLM 接入方案（OpenAI 兼容协议）

LLM 软增强统一走 OpenAI 兼容的 `/chat/completions` 协议，默认 profile 指向
DeepSeek（`https://api.deepseek.com`，模型 `deepseek-chat`）。任何兼容
OpenAI 协议的服务（DeepSeek / OpenAI / Moonshot / 本地 vLLM / Ollama OpenAI 网关
等）只需切换 `base_url` + `model` 即可复用。

实现位置：`servers/memory_server/memory_llm.py`，对外暴露：

- `LLMConfig` / `load_llm_config()`：配置加载
- `LLMClient.chat(messages, ...)` / `LLMClient.complete_text(prompt, ...)`：客户端
- `build_chat_payload()` / `extract_text()`：纯函数原语，便于在不联网情况下
  做 deterministic 单元测试

设计约束：

- 仅依赖 stdlib（`urllib`），不引入运行时新依赖
- 所有 LLM 调用均为可注入 transport，方便测试以 mock 方式覆盖
- LLM 失败必须抛 `LLMRequestError`，由调用方决定是否退化为无 LLM 路径
- 不在日志中输出 API key、不在事件流中持久化 prompt 全文（按需脱敏）

配置来源（按优先级从高到低）：

1. 显式构造 `LLMConfig` 或调用参数 `overrides`
2. 环境变量
   - `MEMORY_LLM_API_KEY`（兼容回落 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`）
   - `MEMORY_LLM_BASE_URL`、`MEMORY_LLM_MODEL`、`MEMORY_LLM_TIMEOUT`
3. 插件根目录本地文件 `MCP/Memory/llm_config.local.json`（**不入 Git**，
   `.gitignore` 已排除；模板见 `llm_config.example.json`）

⚠️ 安全约束：`llm_config.local.json` 与任何 `*.api_key` / `*.apikey` 文件已在
`MCP/Memory/.gitignore` 中标记忽略，禁止把真实 API key 提交到任何版本库。

#### 11.3.2 LLM pipeline 接入主路径（v0.7.0-P4）

实现位置：`servers/memory_server/memory_llm_pipeline.py`，作为 `memory_llm`
原语之上的 token-spend orchestrator，统一处理「同输入不重复花钱」「过长
输入分块汇总」两个工程问题：

- `compute_distill_cache_key(raws, *, model, system_prompt, user_instruction=None)`：
  对 `{model, system, user, records:[{id, content, source, captured_at}, ...]}`
  做 SHA-256；同一组 raw + 同一模型 + 同一 prompt → 同 key，可被进程内
  `DistillCache` 短路。
- `chunk_raw_records(raws, *, max_input_tokens, overhead_tokens=512)`：
  按 token 估算贪心切块，floor `MIN_CHUNK_BUDGET_TOKENS=1024`；输入限制
  超过单 chunk 时自动多 chunk。
- `map_reduce_distill(client, raws, *, record_id, distilled_at, ...) -> dict`：
  单 chunk 直出（避免无谓的 reduce 调用）；多 chunk 时每 chunk 蒸馏后
  以 `REDUCE_SYSTEM_PROMPT` 合并；返回 distilled 记录附
  `pipeline={chunks, llm_calls, cache_hits, reduced}`。拒绝 distilled 输入；
  空集抛 `LLMConfigError`。
- `summarize_records_for_recall(client, records, *, query=None, ...) -> dict`：
  对召回结果走相同的 chunk + map-reduce 概括逻辑。

主路径接入：

| 入口 | opt-in 字段 | 行为 |
|------|------------|------|
| `memory_write` op=`record` | `distill: bool`, `distill_user_instruction`, `distill_max_tokens` | 主写成功后构造 raw view → `map_reduce_distill` → `memory_write_record(record_kind="observation", scope="user_private", derived_from_record_ids=[raw_id])` 落第二条记录；返回 `result.distilled`。失败仅 in-band 报 `llm_unavailable`，主写已落盘不会回滚。 |
| `memory_context` op=`retrieve_context` | `summarize: bool`, `summary_query`, `summary_max_tokens`, `summary_max_chars_per_record`(默认 4000) | 召回成功且有记录后概括 `context_items`/`selected_records`；返回 `result.summary`。无记录 → `summarize_skipped`。 |

硬约束（与 §2.1.A 一致）：

- raw 永远先落盘；LLM 失败绝不影响主写
- distilled 仍 `replaceable=True` + `derived_from=[raw_ids]`
- 默认全部 opt-in；未触发开关 → 0 LLM 调用 / 0 token

### 11.4 MCP 写入接口示例

```json
{
  "operation": "record",
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

### 11.5 MCP 编译接口示例

```json
{
  "operation": "compile",
  "target": "runtime_digest",
  "user": "yangskin",
  "task_id": "task_sp_sync",
  "branch": "feature/sp-roundtrip",
  "include_scopes": ["shared", "personal"],
  "include_statuses": ["validated", "published"],
  "preferred_tags": ["asset_pipeline", "texture"]
}
```

### 11.6 MCP 上下文装配接口示例

```json
{
  "operation": "retrieve_context",
  "query": "texture pipeline",
  "include_scopes": ["shared", "project_shared", "session"],
  "include_statuses": ["raw", "candidate", "validated", "published"],
  "system_area": "memory",
  "module_names": ["MemoryServer"],
  "top_k": 10
}
```

## 12. 无 LLM 兜底方案 + LLM/非-LLM 职责划分

### 12.0 总原则

> **raw 原始信息不可改 → LLM 想接哪个环节都可以；
> 但只在"LLM 真正擅长"的环节用，其他全部由确定性代码完成。**

判断一个能力该不该走 LLM，按这两条筛：

1. **它是不是非 LLM 已经做得足够好的事？** —— 是的话不引入 LLM（确定性、零成本、零延迟）。
2. **它是不是离开 LLM 就根本做不好的事？** —— 是的话由 LLM 做，并在 distilled 层
   落地，可被任何 LLM 重建，不污染 raw。

下面给一份明确的能力矩阵；后续任何"要不要在这里塞 LLM"的争论都按这张表走。

### 12.1 能力矩阵

| 能力 | 由谁负责 | 备注 |
|---|---|---|
| 原始记录写入 / 哈希 / 冻结 | **非 LLM**（`memory_writer` + `make_raw_record`） | raw 由代码生成、由代码冻结，LLM 永远不动 |
| Front Matter 解析、Schema 校验 | **非 LLM**（`memory_frontmatter` / `memory_records`） | 规则即可解决 |
| 事件日志、Lock、Backup、Compactor | **非 LLM**（`memory_events` / `memory_locks` / `memory_backup` / `memory_compactor`） | 写盘安全完全靠 deterministic 链路 |
| 关键词 / FTS 检索 | **非 LLM**（`memory_search` / `memory_retrieval`） | SQLite FTS 已是 BM25 级别基线 |
| 评分 / 排序 / 衰减 | **非 LLM**（`memory_scoring` + `memory_strategy_hash`） | 规则可解释、可回归 |
| Token 估算（输入闸门） | **非 LLM**（`token_estimator`，CJK-aware） | 节省 LLM 调用本身的 prompt token |
| 编译模板 digest（无 LLM 兜底） | **非 LLM**（`memory_compile_*`） | §12.3 描述的"稳但朴素"路径，永远可用 |
| 自然语言摘要 / 重写 / 蒸馏 | **LLM**（`distill_raw_records` → `make_distilled_record`） | 必须 `derived_from` 指回 raw |
| 跨 raw 主题聚类 / 命名 | **LLM**（蒸馏的一种 kind） | 落 distilled，可重做 |
| 冲突识别（语义层） | **LLM 提议 → 非 LLM 校验** | LLM 输出冲突候选；最终判定/落盘走 governance 流程 |
| 自然语言查询解析 | **LLM 提议 → 非 LLM 检索** | LLM 把 NL 查询翻成结构化关键词 / facets，再交给 FTS |
| 链路追溯 / `derived_from` 维护 | **非 LLM**（`memory_lineage`） | 谱系是审计基础，不能让 LLM 决定 |
| 治理（candidate→validated→published） | **非 LLM**（`memory_governance`，可选路径） | 仅历史兼容，新流程默认 raw + distilled |
| 成本/预算控制 | **非 LLM**（`LLMConfig`/`LLMClient` budgets） | 决不能让 LLM 自己决定要不要再花一次钱 |

### 12.2 集成模式

LLM 在系统里扮演的是"插件式蒸馏器"，不是核心路径：

```
                       (deterministic)
   raw 写入 ─► memory_writer ─► immutable raw on disk
                                  │
                                  ▼
   FTS 索引 ─► memory_search       │ (always available)
                                  │
                                  ▼
                       compile 模板 (非 LLM 兜底，§12.4)
                                  │
                                  ├── 没有 LLM？输出"稳但朴素"摘要
                                  │
                                  └── 有 LLM 且任务是 LLM 擅长项？
                                         │
                                         ▼
                                   distill_raw_records
                                         │
                                         ▼
                                  make_distilled_record
                                  (replaceable, derived_from=raw_ids)
```

关键不变量：

- **任意环节失败/关闭 LLM，系统功能不退化为 0**：raw 仍写入、FTS 仍可检索、模板 compile 仍出 digest。
- **LLM 输出从不直接覆盖任何 raw 或非 LLM 索引**：它只能产出 distilled 记录。
- **distilled 记录可丢可重建**：清空 distilled 层并不会损坏 raw 真源。
- **主路径 LLM 接入** (v0.7.0-P4)：`memory_write(distill=true)` 在 raw 落盘后才触发蒸馏；`memory_context(summarize=true)` 在召回结果之上做概括；二者均 opt-in，未触发时 0 LLM 调用。详见 §11.3.2。

### 12.3 必须保证的能力（无 LLM 时）

在无 LLM 条件下，系统仍必须支持：

- 原始记录写入
- Markdown 存储
- Front Matter 解析
- 事件日志记录
- 标签缺省写入
- 规则校验
- SQLite FTS 检索
- 模板式 digest 编译
- 候选验证与发布流程（兼容层）
- Git 协作

### 12.4 无 LLM 时的编译策略

采用模板和规则生成：

- 当前系统规则列表
- 当前用户最近高价值记录
- 当前任务最新状态
- 待验证项
- 当前 handoff 草稿

也就是说，没有 LLM 时，也要能得到“稳但朴素”的编译结果。

### 12.5 单一事实源（代码层）

策略矩阵在代码侧由 `servers/memory_server/memory_llm_policy.py` 落地：

- `LLM_CAPABILITY_MATRIX`：能力 → owner（`"llm"` / `"non_llm"` / `"hybrid"`）
- `should_use_llm(capability) -> bool`：写入路径要不要走 LLM 的统一判断
- 任何后续 PR 想"在 X 处加 LLM"必须先在矩阵里登记并加上对应单测；
  避免散落的 `if has_llm: ...` 把责任划分模糊化。

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

### 阶段 4：P3 结构升级版

目标：

把当前“结构化项目记忆服务器”升级成“证据驱动的联合项目记忆编译器”。这一阶段不依赖 LLM，也不引入向量索引，优先补齐时间、谱系、评分、分层召回和回顾入口。

#### Phase 3A：证据层与记录模型升级

新增 record schema v2 字段：

- `occurred_at`
- `valid_from`
- `valid_to`
- `memory_tier`: `hot | warm | cold | fossil`
- `cognitive_level`: `dao | fa | shu`
- `derived_from_record_ids`
- `derived_from_snapshot_ids`
- `derived_from_revision_ids`
- `supersedes`
- `conflicts_with`
- `related_artifact_ids`
- `importance_score`
- `asset_paths`
- `map_names`
- `plugin_names`
- `module_names`
- `class_names`
- `blueprint_paths`
- `system_area`

扩展 scope：

- `session`
- `user_private`
- `task_or_branch`
- `project_shared`
- `org_shared`

新增 record kind：

- `observation`
- `artifact_ref`
- `incident`
- `decision`
- `procedure`
- `snapshot_daily`
- `snapshot_weekly`
- `snapshot_monthly`

当前首版实现状态：

- `memory_write_record` 已支持 `schema_version="2.0"`，并在使用 P3 record kind、scope 或 v2 字段时自动写入 schema v2 Front Matter。
- 默认写入仍保持 schema `1.0`，旧调用方不受影响。
- 已增加 `memory_tier`、`cognitive_level`、`importance_score` 校验。
- 已支持 v2 时间字段、谱系字段和工程 facet 字段落盘。
- `memory_record_index.py` 已将 schema v2 字段写入 SQLite metadata 表，并把 tier、cognitive level、system area 与 facets 纳入 FTS 检索。
- `memory_write_record` MCP 工具 schema 已暴露 P3A 字段。
- 已实现 `memory_record_observation`，用于以 schema v2 observation 形式记录原始证据。
- 已实现 `memory_link_artifact`，用于给既有记录追加 artifact / 工程 facet，并刷新已有索引。
- 已实现 `memory_trace_lineage`，用于追踪 `derived_from_record_ids`、`supersedes`、`conflicts_with` 记录谱系。

#### Phase 3B：时间快照编译器

新增正式 snapshot：

- `daily_snapshot`
- `weekly_snapshot`
- `monthly_snapshot`

daily snapshot 字段：

- `window_start`
- `window_end`
- `derived_from_record_ids`
- `top_changes`
- `top_reused_memories`
- `open_questions`
- `candidate_for_weekly`

weekly snapshot 字段：

- `derived_from_daily_snapshot_ids`
- `derived_from_record_ids`
- `resolved_this_week`
- `still_open`
- `new_rules`
- `stale_but_relevant`
- `candidate_for_monthly`

monthly snapshot 字段：

- `derived_from_weekly_snapshot_ids`
- `theme_clusters`
- `promoted_knowledge`
- `discarded_paths`
- `architecture_shifts`
- `what_stayed_true`
- `what_changed`

新增编译目标：

- `daily_snapshot`
- `weekly_snapshot`
- `monthly_snapshot`
- `rollback_context`
- `review_queue`
- `dao_digest`
- `fa_digest`
- `shu_digest`

其中：

- `dao_digest`：原则、边界、长期稳定共识
- `fa_digest`：规则、流程、治理、编译秩序
- `shu_digest`：具体做法、操作手册、常用 skill

当前首版实现状态：

- 已实现 `memory_compile(target="daily_snapshot" | "weekly_snapshot" | "monthly_snapshot", as_of=...)`。
- snapshot 输出为 `memory-bank/compiled/snapshots/...` 下的可重建视图，不回写真源记录。
- 已通过 compile cache 记录 `snapshot_id`、`window_start`、`window_end`、`derived_from_snapshot_ids` 和 `included_record_ids`，支持 weekly 派生 daily、monthly 派生 weekly。
- 已实现 `rollback_context`、`review_queue`、`dao_digest`、`fa_digest`、`shu_digest` 编译目标。

#### Phase 3C：importance scoring 与回顾入口

新增 deterministic scorer：

```text
importance = governance + usage + impact + novelty + conflict + decay
```

拆分函数：

- `score_governance()`
- `score_usage()`
- `score_impact()`
- `score_novelty()`
- `score_conflict()`
- `score_decay()`

主要信号：

- 治理信号：candidate -> validated -> published、supersede、conflict
- 使用信号：被 compile 命中次数、被多少任务引用、被多少用户引用、被多少 snapshot 继承
- 影响面信号：涉及多少模块、资产、地图、shared 层
- 事件信号：incident、迁移、回归、接口变化
- 时间衰减信号：新近性 + 老记录复用反增权

新增回顾入口：

- 本日最重要 10 条
- 本周最重要 10 条
- 本月新稳定规则
- 本月高复用旧记录
- 本月被放弃路线
- 当前 open conflicts
- 当前 rollback chain

当前首版实现状态：

- 已新增 `memory_scoring.py`，实现 deterministic scorer：`governance + usage + impact + novelty + conflict + decay`。
- usage stats 继续放在 `.ai-memory/usage-stats.json`，compile 命中会累加 `compile_hit_count` 和 `compile_targets`，不修改源记录。
- `review_queue` 使用 scorer 输出本轮最重要记录、hot tier、新稳定规则和 discarded paths。
- `dao_digest`、`fa_digest`、`shu_digest` 按 `cognitive_level` 生成分层视图。

#### Phase 3D：检索升级为上下文装配

新增工具：

- `memory_record_observation`
- `memory_link_artifact`
- `memory_trace_lineage`
- `memory_compare_snapshots`
- `memory_list_conflicts`
- `memory_retrieve_context`

当前首版实现状态：

- 已实现 `memory_record_observation`
- 已实现 `memory_link_artifact`
- 已实现 `memory_trace_lineage`
- 已实现 `memory_context(operation="list_conflicts")`，用于列出 `conflicts_with` 冲突边、缺失目标和 resolved 状态
- 已实现 `memory_context(operation="compare_snapshots")`，基于 compile cache 对比两个 snapshot 的 added / removed / persisted record ids
- 已实现 `memory_context(operation="retrieve_context")`，按固定 pipeline 输出 budget-first 上下文装配结构

`memory_retrieve_context` 固定顺序：

1. scope filter
2. time window filter
3. facet filter
4. metadata / FTS recall
5. importance rerank
6. budget-first packing
7. context assembly

当前输出策略：

- `context_items` 是唯一正文展开层，受 `max_chars` / `max_tokens` / `max_items` 严格约束。
- `core_constraints` / `relevant_rules` / `key_evidence` 保留为分类索引，只返回元数据和 `context_item_id`，避免同一条记录正文重复占预算。
- 返回 `budget_report`、`dropped_candidates`、`evidence_refs`，与 `important_memories` 共用预算打包原语。

输出结构：

- `core_constraints`
- `relevant_rules`
- `recent_snapshots`
- `key_evidence`
- `open_conflicts`
- `next_steps`

#### P3.5：重要记忆输出接口（新的最高优先级）

目标：

- 为 LLM / agent 提供稳定、可控、可复用的重要记忆输出接口
- 把“记忆查询”升级为“budget-first 的重要记忆供给”
- 明确区分“插件负责动态记忆”与“外部系统负责项目规则”

建议接口：

- `memory_context(operation="important_memories")`

建议输入：

- `query`
- `max_tokens`
- `max_chars`
- `max_items`
- `window_start` / `window_end`
- `include_scopes` / `include_statuses`
- facet 过滤字段

建议输出：

- `important_memories`
- `reason_selected`
- `importance_score`
- `evidence_refs`
- `suggested_externalization`
- `dropped_candidates`
- `budget_report`

装配顺序：

1. scope / task / branch 过滤
2. time window 过滤
3. facet 过滤
4. metadata / FTS recall
5. importance rerank
6. retention cutoff
7. budget-first packing
8. 去重与降级渲染

接口目标不是自动审查或自动发布，而是输出“值得人或外部 agent 继续沉淀”的重要记忆。

职责边界：

- `retrieve_context`：面向日常会话，返回简洁可直接消费的上下文
- `important_memories`：只在需要提取“当前重要信息”时返回详细结果
- maintenance / governance / admin：作为兼容层保留，不要求普通用户参与

#### P3+：多人联合项目治理增强（降级为兼容方向）

目标：

- 保持现有治理链路可用，服务历史数据、兼容测试和管理场景
- 把治理动作迁移到 CLI / scripts / 管理 skill
- 不再把插件内自动审查和规则晋升当作主产品方向

晋升逻辑：

- `shu`：允许较快沉淀，优先从 observation / procedure 产生
- `fa`：要求多次复用或多人验证
- `dao`：只能从稳定 fa 上升，且必须人工确认

### 阶段 5：P4 LLM 软增强版

目标：

- query rewrite
- tag / facet 推荐
- candidate draft
- snapshot narrative
- conflict explanation
- title / abstract 优化

限制：

- 不直接发布正式系统记忆
- 不覆盖真源
- 不替代 deterministic compile
- 不直接决定 dao 层内容
- 无 LLM 时，基础写入、检索、快照、评分、编译和治理必须继续可用

### 阶段 6：P5 本地 RAG / 向量补召回版

目标：

- 语义模糊召回
- 长尾别名补召回
- 低关键词命中场景下的 recall 增强

限制：

- 向量索引只放 `.ai-memory/`
- 可删除、可重建
- 永远不是正式真源
- 检索顺序仍是 metadata -> FTS -> vector supplement -> rerank

### 15.5 与当前仓库的落地顺序建议

结合 `ToolTest/MCP/Memory` 当前已落地的 P3 首版与 hardening 结果，建议按以下顺序推进：

0. **已完成：`retrieve_context` 升级为严格 budget-first**
   - `memory_context(operation="important_memories")` 已上线，返回体受 `max_tokens` / `max_chars` / `max_items` 约束，输出最重要记忆、证据引用、丢弃原因和预算报告。
   - `memory_context(operation="retrieve_context")` 已对齐同一套预算路径，不再以 `top_k` 作为主控制；`top_k` 仅作为未显式传 `max_items` 时的条数上限。
   - `retrieve_context` 保持简洁：正文集中在 `context_items`，分类段落只做索引；详细外部沉淀信号仍通过 `important_memories` 提供。
1. **MCP 对外接口收敛**（首版已完成）
   - 默认 MCP 只暴露 `memory_read`、`memory_write`、`memory_context` 三个 facade tools。
   - legacy/admin 兼容工具降为内部函数或显式配置暴露。
   - guard、backup、compact、index、health、migrate、governance、snapshot 管理动作迁移到 CLI / scripts / skill。
   - 已增加配置开关 `mcp.expose_admin_tools=true` 支持开发期或纯 MCP 客户端继续暴露 admin/legacy tools。
2. 已保留内部实现稳定性，不立即删除已验证函数，默认只通过 facade 暴露。
3. 已在现有文件级接口上增加记录级抽象，旧调用仍可继续用。
4. 已补齐 `Front Matter` 解析、记录落盘规则、SQLite FTS 派生索引、schema v2 字段。
5. 已实现 `runtime digest`、`task handoff`、`system_digest`、`publish_queue` 和 P3 snapshot/review/rollback/dao-fa-shu 编译目标。
6. P3 首版已完成：schema v2、时间快照、谱系关系、importance scoring、facet、回顾入口和上下文装配。
7. 已完成多轮内部拆分与并发安全加固：`memory_compiler.py` 已拆出 cache / render / targets / scoring / writer / views，主文件已降为 ~330 行 thin orchestration router（v0.5.9），`server.py` 已拆为 `server_descriptions` / `server_tools` / `server_dispatch`（v0.5.3），`memory_frontmatter.py` / `memory_budget.py` / `memory_corpus.py` / `memory_locks.py` / `memory_request_id.py` 已拆出；`retrieve_context` 已升级为严格 budget-first（v0.5.8）；管理 skill `memory-admin` / `memory-snapshot-review` 首版上线（v0.5.9）。
8. **v0.5.11 非 UE 稳健性收尾（已完成）**
   - 已修复旧 `.ai-memory/config.json` 覆盖 `guard.targets` 且缺少 `write_policy` 时的 guard 兜底不一致：`memory_guard_check` / `check_total_budget` 像 `memory_write` 一样参考默认 `multi_user.user_scoped_paths` 与 `shared_paths_policy`。
   - 已给 `retrieve_context` / `important_memories` 增加 SQLite metadata/facet 预筛能力：当索引存在且健康时，先从 `memory_records` 表按 scope/status/user/task/branch/system_area/facet 缩小候选集，再回到 Markdown 真源做确定性排序与预算打包；索引缺失或异常时无损回退到全量 Markdown 扫描。
   - 已建立非领域绑定的规模性能基线：`run_memory_scale_smoke.py --records 5000` 覆盖 5k 结构化记录的 rebuild、FTS search、metadata/facet 预筛 retrieval，避免后续退化成不可见的 O(N) 热路径。
   - 已补齐恢复演练文档：损坏索引、超预算、审计日志轮转、误发布/误删除的 CLI 恢复步骤可从 README / admin skill 执行。
   - 已修正文档时间线，确保 DEVLOG 与设计文档中的版本日期不晚于实际记录日期。
9. 治理相关能力保留为兼容层：validate / publish / archive / promote / degrade 不再作为主路线扩张。
10. P4 再接入本地模型或云端 LLM，把 LLM 能力限制在 query rewrite、tag/facet 推荐、重要记忆摘要优化、快照叙事和冲突解释。
11. P5 最后评估本地 RAG / 向量补召回；RAG 只能作为 metadata / FTS 后的语义补充。

### 15.6 P3 模块落地状态

已修改现有模块：

- `memory_records.py`：已扩 schema v2，支持 snapshot、lineage、tier、cognitive_level、facet。
- `memory_record_index.py`：已索引 schema v2 metadata / facets，并支持损坏 SQLite 索引自愈。
- `memory_compiler.py`：保留 compile 主入口与 snapshot / review / rollback / dao-fa-shu 编译流程；render / targets / scoring 已拆出独立模块。
- `memory_governance.py`：已保留 validate / publish / archive 治理；promote / degrade 进入 P3+。
- `memory_maintenance.py`：已支持 health / migrate / tombstone delete。
- `memory_events.py`：继续作为审计日志底层。

已新增模块：

- `memory_scoring.py`
- `memory_lineage.py`
- `memory_retrieval.py`
- `memory_record_io.py`
- `memory_corpus.py`（v0.5.2：CompilableRecord / compact_body / iter）
- `memory_frontmatter.py`（v0.5.3：Front Matter parser/dumper）
- `memory_budget.py`（v0.5.3：budget 原语，retrieve_context / important_memories 共享）
- `memory_compiler_cache.py`（v0.5.3：compile cache + usage stats IO）
- `memory_compile_targets.py`（v0.5.8： target 常量 / slug / compiled path）
- `memory_compile_render.py`（v0.5.8：runtime / task / system / publish render）
- `memory_compile_scoring.py`（v0.5.8：record time / sort key / scored records）
- `memory_compile_writer.py`（v0.5.9：cache_key + write_compiled_view 纯 IO 层）
- `memory_compile_views.py`（v0.5.9：snapshot/level/review/rollback compile 视图 + memory_compare_snapshots）
- `memory_locks.py`（v0.5.4：跨进程 sidecar 文件锁，POSIX fcntl / Windows msvcrt + sha1 路径 + 同线程引用计数可重入）
- `memory_request_id.py`（v0.5.4：UUID7 + content_sha 供乐观锁）
- `server_descriptions.py` / `server_tools.py` / `server_dispatch.py`（v0.5.3：`server.py` 拆分）

暂不新增独立 `memory_snapshots.py` / `memory_facets.py`，snapshot 逻辑先收在 `memory_compiler.py`，facet 逻辑先由 record schema / index / retrieval 共同承载。后续内部拆分时再按复杂度拆出。

### 15.6.1 P0 内部重构（2026-04-23 起）

P3 完成后，对内部实现进行不改变 MCP 对外契约的轻量重构。目标：消除重复 IO、收敛超大文件、为后续拆分铺路。

P0-3 已完成（2026-04-23）：

- 新增 `memory_record_io.py` 作为唯一的 record IO 公共层。
- `iter_record_files` / `iter_parsed_records` / `find_record_by_id` / `refresh_index_if_exists` / `write_same_record` / `write_record_to_target` 在此集中实现。
- `memory_governance.py` / `memory_lineage.py` / `memory_maintenance.py` / `memory_compiler.py` 不再各自实现这些函数。
- 默认 MCP facade 工具数仍为 3 个；P0-3 完成时测试 133 全部通过。

P0-4 已完成（2026-04-23）：

- 深度健壮性测试新增 `tests/memory_server/test_robustness_deep.py`。
- `PathManager.resolve` 前置拒绝 NUL 字节，避免 `pathlib` 抛 `ValueError` 越过安全层。
- `memory_rebuild_index` 在 rebuild 前用 `PRAGMA integrity_check` 探测损坏 SQLite，并自动清理坏 db / WAL / SHM 后重建。
- 覆盖并发 overwrite、残留 `.tmp`、坏 Front Matter 跳过、空 corpus not_found、路径攻击、Unicode 往返、全局预算拒绝、非法 record_kind 不落盘。
- 当前 Memory 测试为 146 全部通过。

P0-1 / P0-2 进度：

- P0-1 已完成（v0.5.3 / v0.5.8 / v0.5.9）：`memory_compiler.py` 已拆出 cache / render / targets / scoring / writer / views；snapshot / level / review / rollback 全部出栈，主文件由 ~774 行降到 ~330 行的 thin orchestration router。
- P0-2 已完成（v0.5.3）：`server.py` 从 1348 行降到 ~102 行 thin entry-point；拆出 `server_descriptions.py`（常量 / 描述）、`server_tools.py`（facade / legacy schema）、`server_dispatch.py`（参数校验 / 路由）；back-compat re-exports 保证 11 个测试模块旧 import 继续生效。
- P1-3 已完成（v0.5.2）：`CompilableRecord` 与 `compact_body` 已上提到 `memory_corpus.py`，`memory_retrieval` 不再反向依赖 compiler 私有函数。
- P1-5 已完成（v0.5.3）：Front Matter parser/dumper 已抽到 `memory_frontmatter.py`，`memory_records.py` 通过 re-export 保持符号兑现。

P0-2 鲁棒性补丁（v0.5.2）：

- `personal` 与 schema v2 `user_private` 统一按作者隔离：compiler `_matches_filter` 与 retrieval `_collect_records` 同步使用 `private_scopes = {"personal", "user_private"}`，并新增回归测试 `test_p3_private_scopes_isolate_authors_in_retrieval`。
- 防御 `important_memories` / `retrieve_context` 在多租户 corpus 下越权读取私有记忆。

### 15.7 P3/P4 测试计划与当前覆盖

P3A 测试：

- schema v2 兼容旧记录
- 新字段缺省值与迁移
- dao / fa / shu 字段校验
- memory_tier 校验
- artifact / observation 落盘与读取
- 当前状态：已覆盖。

P3B 测试：

- daily / weekly / monthly snapshot 生成
- derived_from 链完整性
- snapshot 重建一致性
- snapshot 不回写真源
- 当前状态：已覆盖。

P3C 测试：

- importance scoring 稳定性
- usage / governance / impact 组合打分
- hot / warm / cold / fossil 路由正确
- 当前状态：已覆盖首版 scorer 和 review/dao-fa-shu 输出。

P3D 测试：

- `memory_retrieve_context` 装配顺序
- scope / time / facet / FTS / rerank 结果正确
- rollback context 生成
- 当前状态：已覆盖。

Hardening 测试：

- 并发 overwrite 原子性
- 残留 `.tmp` 不影响读取/遍历
- 坏 Front Matter / 非 record Markdown 跳过
- 空 corpus `find_record_by_id` 返回 `not_found`
- 损坏 `search.db` rebuild 自愈
- NUL / 绝对路径 / `..` 等路径攻击拒绝
- Unicode 往返
- 全局预算拒绝
- 非法 `record_kind` 不落盘

P4 测试：

- LLM 不可用时主链路不受影响
- LLM 只增强、不改变发布权限

### 15.8 建议版本路线

`v0.5.0`：结构升级版

- schema v2
- observation / artifact_ref / incident
- snapshot_daily / weekly / monthly
- lineage 基础字段
- importance scoring v1
- hot / warm / cold / fossil
- `memory_retrieve_context` v1

`v0.5.1`：内部重构与健壮性加固版

- record IO 公共层
- NUL 路径拒绝
- 损坏 SQLite 索引自愈
- 深度健壮性测试
- 当前测试数 143

`v0.5.2`：corpus 解耦与私有作用域隔离修复版

- 抽出 `memory_corpus.py`（CompilableRecord / compact-body / iter）
- `memory_retrieval` 不再依赖 compiler 私有符号
- `personal` / `user_private` 按作者严格隔离（compiler + retrieval 双修）
- `memory_context(operation="important_memories")` 首版上线
- 当前测试数 146

`v0.5.3`：单进程并发与索引去重整理版（已上线）

- 内部模块继续解耦：`memory_compiler_cache` / `memory_record_index` / `memory_writer` 边界澄清
- 索引重建去重 + 并发写场景的同进程线程互斥
- 当前测试数 191

`v0.5.4`：单机多 agent + 单 mcp 规格并发安全版（已上线）

- 跨进程 sidecar 文件锁 `memory_locks.file_lock`（POSIX fcntl / Windows msvcrt，sha1 路径 → `.ai-memory/locks/`）
- SQLite WAL + busy_timeout=30000ms
- 乐观锁 `if_match=<sha256>` + `error="conflict"` 回退
- `request_id = str(uuid.uuid7())`（Python 3.14 原生）
- audit event 强制写入 request_id / new_sha / if_match
- 当前测试数 196

`v0.5.5`：写入崩溃安全 + fsync 严格模式版（已上线）

- `_atomic_write_text`：同目录 tmp + `O_CREAT|O_EXCL|O_WRONLY`（+`O_BINARY`）+ 数据 fsync + `os.replace` + 父目录 fsync（POSIX）
- 配置开关 `mcp.fsync_strict`：true 时 fsync OSError 直接传播，保证崩溃 / 断电后无外部可见破损写入
- `memory_writer` / `memory_compiler_cache.usage_stats` / `memory_maintenance.tombstones` / `memory_events` 全部走统一 atomic 路径
- 当前测试数 204

`v0.5.6`：多 agent 严格压力 + 锁/磁盘 P0-P2 守卫版（已上线）

- 9 项多 agent 压力测试（spawn 多进程、events.jsonl 真追加、if_match 重试、混合负载、长路径）
- bug 修复：`file_lock` 在 yield 异常路径上 fd 泄漏（重写为嵌套 try/finally）
- bug 修复：`memory_events` 原 `msvcrt.locking` 1 字节锁锁的是各自当前位置 → 多进程不互斥；改用统一 `file_lock`
- Windows `os.replace` 在读者持有无 `FILE_SHARE_DELETE` 句柄时短暂 PermissionError → 20×10ms 重试
- `PathManager` 长路径 `\\?\` / `\\?\UNC\` 前缀规范化；`safe_read_text` 有界重试覆盖 Windows rename 短窗口
- P0/P2 follow-up：`_acquire_os_lock` 改 `except BaseException`，`KeyboardInterrupt` / `SystemExit` 不再泄漏 sidecar fd；新增 `DiskFullError(OSError)` + `_DISK_FULL_ERRNOS = {ENOSPC, EDQUOT, EFBIG}`，`memory_write` 返回结构化 `error="disk_full"` + `errno`
- 当前测试数 216

`v0.5.7`：默认多人安全策略版（已上线）

- `multi_user.enabled` 默认改为 `true`，新工作区不需要额外配置即可按用户分流 `activeContext.md`
- `memory_write` 的策略判断增加 `multi_user.user_scoped_paths` 兜底，兼容旧 `.ai-memory/config.json` 覆盖 `guard.targets` 但没有 `write_policy` 的情况
- 共享 warm 文件继续通过默认 `shared_paths_policy` 强制 overwrite → append 降级
- 新增 2 项默认多人模式回归测试
- 当前测试数 218

`v0.5.8`：retrieve_context budget-first + compiler 首轮拆分版（已上线）

- `memory_retrieve_context` 始终先按预算打包 `context_items`，默认预算复用 important-memory 原语；`top_k` 仅作为默认 `max_items`
- `retrieve_context` 返回 `budget_report` / `dropped_candidates` / `evidence_refs`，分类段落不再重复展开正文
- 新增 `memory_compile_targets.py`、`memory_compile_render.py`、`memory_compile_scoring.py`
- `memory_compiler.py` 主入口由约 929 行降到约 724 行；对外 compile / digest / snapshot 行为不变
- 当前测试数 218

`v0.5.9`：compiler 二轮拆分 + 管理 skill 首版（已上线）

- `memory_compiler.py` 二轮拆分：拆出 `memory_compile_writer`（cache_key + write_compiled_view 纯 IO 层）与 `memory_compile_views`（snapshot/level/review/rollback compile 视图 + memory_compare_snapshots）
- 主文件由 ~774 行降到 ~330 行的 thin orchestration router（13→3 函数：`_matches_filter` / `memory_compile` / `memory_get_runtime_digest`）；通过 `__all__` re-export 保持 `memory_compare_snapshots` / `find_compile_cache_entry` / `get_record_last_used_at` / `load_compile_cache_entries` 对 server_dispatch 与测试的兼容
- 死代码清理：`memory_events.py` 移除遗留的 `_lock_file` / `_unlock_file` / 未用 `import sys`
- 新增管理 skill：`.github/skills/memory-admin/SKILL.md`（guard / backup / compact / health / index / governance / snapshot 维护流程）与 `.github/skills/memory-snapshot-review/SKILL.md`（review_queue 走查 + 历史快照重放）
- 当前测试数 218（无回归）

`v0.5.10`：管理 CLI 入口版（已上线）

- 新增 `servers/memory_server/cli.py`：argparse 一级入口，覆盖 `guard` / `health` / `backup` / `compact` / `rebuild-index` / `migrate` / `validate` / `publish` / `archive` / `delete` / `compile` / `snapshot-rebuild` / `runtime-digest`
- 默认 JSON 输出（`--pretty` 缩进）；exit code 0/1 与 `ok=true/false` 一致，便于 CI / shell 脚本消费
- skill `memory-admin` / `memory-snapshot-review` 同步改为 CLI 调用范式，admin 能力不再依赖 `mcp.expose_admin_tools=true`
- 新增 `tests/memory_server/test_cli.py`：9 项 CLI 行为回归（health / guard / backup / rebuild-index / compile / snapshot-rebuild / pretty / 错误码）
- 当前测试数 230

`v0.5.11`：非 UE 稳健性收尾版（已上线）

- guard 旧配置兜底一致性：旧配置没有 `write_policy` 时，guard 与 total budget 同样按默认多人策略处理 user-scoped 与 append-only 共享文件。
- retrieval 规模基线与预筛：新增 SQLite metadata/facet 候选预筛，优先使用可重建索引减少 Markdown 全量解析压力，失败时回退全量扫描。
- 管理文档补强：README / admin skill 补恢复演练与性能基线入口，DEVLOG 修正时间线并记录本轮结果。
- 新增旧配置 guard 回归、索引预筛正确性/回退回归、5k 记录级性能冒烟脚本；全量测试 230 通过。

### 15.9 v0.6.0 开箱即用稳健性版（规划，最高优先级）

#### 15.9.1 总目标

普通使用者唯一需要显式做的事 = 在 `.vscode/settings.json` 设置 `memory-mcp.userName`。其余团队约定（共享文件 append-only、周期性维护、UE facet 词典、规模基线等）必须由插件自动兜底；缺省状态下不允许"默默落到 `unknown.md`"或"无声 overwrite 共享文件"等隐性数据风险。

#### 15.9.2 P0 — 健壮性 / 数据安全（必须 TDD）

| 编号 | 项目 | 行为 | 测试位置 |
|---|---|---|---|
| P0-1 | user id 强校验 | `memory_users.is_placeholder_user(name)` 纯函数：当 `name` 落入 hard-reject 集合（`""` / 仅空白 / `unknown` / `Unknown` / `UNKNOWN`）或包含路径注入字符（`/` `\` `:` `\n` `\r` `\0`）时返回 `True`。`memory_users.validate_effective_user(config)` 在每次 facade 调用前运行：hard-reject 时返回结构化 `error="user_not_configured"` + `setup_hint`（含 PowerShell 一行命令）阻断写路径；常见模糊用户名（`Administrator` / `User` / `admin` / `root` / `guest` / `default`）不阻断但通过 `events.jsonl` 写一次 `user_ambiguous` warning。`mcp.allow_unknown_user=true` 显式覆盖（不推荐）。 | `tests/memory_server/test_user_validation.py` |
| P0-2 | shared overwrite 强制拒绝 | `memory_write`：当目标命中 `shared_paths_policy=append_only`（含旧/新/缺省三种配置来源）且 `mode="overwrite"`，立即返回 `error="shared_overwrite_forbidden"` + `suggested_operation="record"` + `target_user_scoped_path`（如适用）；不写盘、不备份、不锁。 | `tests/memory_server/test_shared_overwrite_rejected.py` |
| P0-3 | 启动期 auto-maintenance | `memory_auto_maintenance.run_if_due(config)`：检查 `.ai-memory/last_maintenance.json`，超过任一阈值（默认 `min_interval_hours=168`、`events_max_bytes=50MB`、`index_stale_seconds=600`、`shared_append_max_lines=2000`）则按需触发 `health_check` / `rebuild_index` / `compact_memory`；所有子动作幂等，单项失败记录到 `events.jsonl` 但不阻塞主链路；写新的 `last_maintenance.json`。配置 `mcp.auto_maintenance.enabled=false` 可关闭。 | `tests/memory_server/test_auto_maintenance.py` |

#### 15.9.3 P1 — 易用性自动化

| 编号 | 项目 | 关键行为 |
|---|---|---|
| P1-1 | `bootstrap.ps1` | 单脚本：venv → 装依赖 → 询问 user id（仅一次，幂等）→ 写 `.vscode/mcp.json` + `.vscode/settings.json` → 跑一次 health_check 输出绿灯。 |
| P1-2 | UE facet 自动推断 | 检测根目录 `*.uproject` 时扫描 `Source/**/*.Build.cs` + `Plugins/*/*.uplugin`，写 `.ai-memory/ue_facets.json`：`{module_names, plugin_names, system_areas}`。`memory_write_record` 在传入 facet 不在词典时给 warning 不阻塞。 |
| P1-3 | shared append auto-compact | 共享 append 文件超过阈值时自动 fold：保留近 N 周，旧条目移到 `memory-bank/archive/<basename>-YYYYWW.md`，并在原文件头部写 `<!-- archived <range> -> <archive_path> -->`。 |
| P1-4 | 配置完全可选 + diagnose | 缺失 `.ai-memory/config.json` 时全部走默认；`memory_context.config_diagnose` 返回每条策略的来源 (`default` / `file` / `env`)。 |
| P1-5 | `link_artifact` 路径归一化 | UE `/Game/X` ↔ 物理 `Content/X.uasset` 双向解析；附 `git_sha`（如可获取）。无 git 时 graceful。 |

#### 15.9.4 P2 — 稳健性观测

| 编号 | 项目 | 关键行为 |
|---|---|---|
| P2-1 | `cli scale-baseline` | 跑 5k/20k/50k 规模 smoke 写 `.ai-memory/baseline.json`；health_check 对比当前与基线，回归 50% 报 `warning="perf_regression"`。 |
| P2-2 | health 启动自愈 | 异常时尝试 `rebuild_index` / 清孤儿 `.tmp` / 清过期 lock sidecar 后再上报；自愈过程写 `events.jsonl`。 |
| P2-3 | 策略哈希一致性提示 | 策略 schema 哈希写 `events.jsonl`；server 启动时如发现近 N 条事件来自不同策略哈希，警告（不阻塞）。 |

#### 15.9.5 测试与发布纪律

- **TDD 强制**：每个 P0/P1/P2 项目必须先提交失败测试，再提交最小实现，再回归全量。
- **不破坏 facade**：所有新行为通过现有 4 facade 工具（`memory_read` / `memory_write` / `memory_context` / `memory_enhance`）表达；admin / CLI 是兼容入口。
- **不依赖 LLM / 网络**：所有自动化在离线环境必须可运行。
- **错误返回结构化**：`{ok: false, error: "...", request_id: "...", hint: "..."}`，禁止 raise 到 MCP 边界。
- **每完成一项**：DEVLOG 加条目（含测试增量），README 更新版本号与测试数。

`v0.6.0`：开箱即用稳健性版（**当前最高优先级，进行中**）

> **目标：使用者唯一显式配置 = 自己的 user id；其余团队约定全部由插件自动兜底。** 详见 §15.9。

P0（健壮性 / 数据安全，必须 TDD）：

- P0-1 user id 强校验：`load_config` 在解析后强制校验 effective user，落入占位集合（`""` / `unknown` / `Administrator` / `User` / 含空白 / 含 `\` `/` 等路径分隔符）时返回结构化 `error="user_not_configured"` + `setup_hint`，禁止默默写入 `unknown.md`。
- P0-2 shared 文件 overwrite 强制拒绝：`shared_paths_policy=append_only` 命中文件且 `mode="overwrite"` 必须返回 `error="shared_overwrite_forbidden"`，给出 `record` 操作引导；新增/旧/缺省配置必须三种全部生效。
- P0-3 启动期 auto-maintenance：MCP server 启动时检测 `.ai-memory/last_maintenance.json`，超阈值（默认 7 天 / events.jsonl > 50MB / index 落后 mtime）自动触发 `health_check` + `rebuild_index` + 共享 append 文件 auto-compact；所有动作幂等，结果落 `events.jsonl`，失败不阻塞主链路。

P1（易用性自动化）：

- P1-1 `bootstrap.ps1` 单一部署入口：合并 deploy + setup_mcp + user id 询问 + 一次 health 验证。
- P1-2 UE facet 自动推断：检测到根目录 `*.uproject` 时扫描 `Source/**/*.Build.cs` + `Plugins/*/*.uplugin`，写 `.ai-memory/ue_facets.json`，作为 `memory_write_record` 的 facet 推断词典；未知 module 给 warning 不阻塞。
- P1-3 共享 append 文件 auto-compact：超过 `auto_compact_threshold_lines`（默认 2000）自动 fold 旧条目到 `memory-bank/archive/<file>-YYYYWW.md`。
- P1-4 配置完全可选 + `memory_context.config_diagnose`：报告当前生效策略来源（默认 vs 文件覆盖）。
- P1-5 `link_artifact` 自动归一化：UE `/Game/...` ↔ 物理 `Content/...` 路径双向解析 + 附 git sha。

P2（稳健性观测）：

- P2-1 `cli scale-baseline`：一键跑 5k/20k/50k 规模 smoke，结果写 `.ai-memory/baseline.json`；后续 health_check 与基线对比，慢 50% 告警。
- P2-2 health 启动自愈：异常时尝试 `rebuild-index`、清理孤儿 `.tmp`、清理过期 lock sidecar 后再上报。
- P2-3 多 server 实例策略哈希一致性提示：策略 schema 哈希写入 `events.jsonl`，不一致时启动期警告。

`v0.7.0`：LLM 软增强版（规划中）

- query rewrite
- tag / facet 推荐
- snapshot narrative
- conflict explanation
- candidate draft

`v0.7.5`：向量补召回版

- 本地向量索引
- FTS 失败时语义补召回
- 规则重排融合

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

下一阶段优先级：

- 已完成：`retrieve_context` 与 `important_memories` 的预算路径对齐为严格 `budget-first`、可解释、可控尺寸的输出接口。
- 当前默认使用方式为免维护：普通用户不需要执行任何 maintenance / governance / admin 操作。
- MCP 对外接口收敛首版已完成：默认只暴露 `memory_read`、`memory_write`、`memory_context`，其余管理能力迁移到 CLI / scripts / skill，并保留 `mcp.expose_admin_tools=true` 用于 legacy/admin tools。
- P3 首版已完成：时间快照、谱系关系、importance scoring、facet、分层召回、上下文装配和 dao/fa/shu 视图已落地。
- 当前下一步是 P4 LLM 软增强；`memory_corpus.py` 已在 v0.5.2 完成，`memory_frontmatter.py` / `memory_budget.py` / `server_*` 拆分已在 v0.5.3 完成，render / targets / scoring 已在 v0.5.8 完成，并叠加 v0.5.4-v0.5.6 的并发 / 崩溃安全加固（跨进程 sidecar 文件锁、SQLite WAL、乐观锁 if_match、fsync_strict、disk_full 结构化错误、PathManager 长路径归一化），以及 v0.5.11 的 guard 旧配置兜底与 retrieval 预筛。
- 插件内治理扩展降级为兼容方向：保留 validate / publish / archive 能力，但不再把自动审查和规则晋升作为主路线。
- P4 再做 LLM 软增强：query rewrite、tag/facet 推荐、重要记忆摘要优化、快照叙事和冲突解释。
- P5 最后做本地 RAG / 向量补召回，且只作为 metadata / FTS 后的语义补充。

### 16.1 对当前项目的直接意义

对 `ToolTest/MCP/Memory` 而言，这份设计文档的作用不是替代当前 README，而是补齐“当前版本为何存在、下一阶段要长成什么样、迁移过程中如何兼容现有调用方式”这三件事。后续如果继续开发 Memory MCP，应以本文作为 vNext 架构基线，并把 README 继续作为当前实现说明维护。
