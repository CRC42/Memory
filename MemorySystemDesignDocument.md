# MCP 记忆系统设计文档

> 状态：vNext 设计稿；P0/P1/P2、P3 结构升级首版与 P3 hardening 已落地
>
> 日期：2026-04-23
>
> 适用范围：`ToolTest/MCP/Memory` 后续演进设计、重要记忆输出、MCP 对外接口扩展

## 0. 当前项目基线

本设计文档不是脱离现状的重写方案，而是建立在当前 `MCP/Memory` 已有实现之上的下一阶段演进方案。

### 0.1 已有实现

当前仓库中的 Memory MCP 默认对外暴露 3 个 facade tools，并保留 legacy/admin 兼容工具：

- `memory_read`：读取文件、普通搜索、结构化记录搜索、读取 runtime digest
- `memory_write`：普通文件写入、结构化记录写入、observation 证据写入、artifact/facet 关联
- `memory_context`：编译 runtime / snapshot / review 视图、读取 digest、追踪 lineage、列出 conflicts、对比 snapshot、装配上下文
- `mcp.expose_admin_tools=true`：开发期或纯 MCP 客户端兼容完整 legacy/admin 工具集

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

当前版本已经解决了“多人协作下的高频写入冲突”“基础读写、检索、压缩、预算保护”“结构化记录治理”“P3 证据驱动上下文装配”和“深度健壮性边界”问题，但仍存在以下演进空间：

- 内部实现仍有大文件：`memory_compiler.py`、`server.py` 需要继续拆分
- `memory_retrieval` 已切到 `memory_corpus.CompilableRecord` / `compact_body`，不再依赖 compiler 私有符号；后续可继续把 corpus 与 compiler 之间的接口稳定化
- Front Matter parser/dumper 仍在 `memory_records.py`，后续应抽 `memory_frontmatter.py`
- `memory_retrieval` 仍是 `top_k` 驱动，尚未升级为严格 `budget-first` 的小上下文输出（`important_memories` 已 budget-first）
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

### 11.0 对外接口收敛（已完成）与重要记忆输出（新的最高优先级）

当前实现为了验证功能与测试覆盖，曾把内部能力较细粒度地暴露为多个 MCP tools。这个形态适合开发期调试，但不适合作为长期对外接口：AI 客户端会看到过多工具，容易误选高风险管理动作，也会让工具 schema 变得嘈杂。

当前首版已完成：**MCP 对外默认只保留运行时高频读写与上下文能力，管理能力迁移到 CLI / scripts / skill。**

在此基础上，后续最高优先级不再是扩展插件内治理，而是补齐“重要记忆输出”接口：让 LLM / agent 能在严格预算内拿到之前最重要的动态记忆，再由外部流程决定是否沉淀为项目规则。

默认 MCP 对外只暴露 3 个 facade tools：

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
- 开发期可通过配置开关继续暴露完整工具集：`mcp.expose_admin_tools=true`，当前兼容工具数为 23（含 3 个 facade）。
- 默认 MCP tool list 收敛为 3 个 facade。
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

- `memory_classify_record`
- `memory_extract_candidate`
- `memory_merge_candidates`
- `memory_generate_skill_candidate`
- `memory_explain_conflict`
- `memory_generate_handoff`

这些接口失败时不得影响基础链路。

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
- 已实现 `memory_context(operation="retrieve_context")`，按固定 pipeline 输出上下文装配结构

`memory_retrieve_context` 固定顺序：

1. scope filter
2. time window filter
3. facet filter
4. metadata / FTS recall
5. importance rerank
6. context assembly

当前局限：

- 仍以 `top_k` 为主，而不是严格预算优先
- `core_constraints` / `relevant_rules` / `key_evidence` 可能重复展开同一条记录
- 返回结构更像“上下文草图”，还不是稳定的小预算“重要记忆输出”

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

0. **最高优先级：把 `retrieve_context` 升级为严格 budget-first**
   - `memory_context(operation="important_memories")` 已上线（v0.5.2），返回体受 `max_tokens` / `max_chars` / `max_items` 约束，输出最重要记忆、证据引用、丢弃原因和预算报告。
   - 待做：把 `retrieve_context` 的预算路径与 `important_memories` 对齐，不再以 `top_k` 为主；并扩展 `evidence_refs` 来源（不仅 `source_refs`）。
   - `retrieve_context` 保持简洁；详细结果通过 `important_memories` 提供。
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
7. 下一步优先做内部拆分与重要记忆供给增强：拆 `memory_compiler.py` / `server.py`，抽 `memory_frontmatter.py`，把 `retrieve_context` 升级为严格 budget-first（`memory_corpus.py` 抽离已完成 v0.5.2）。
8. 治理相关能力保留为兼容层：validate / publish / archive / promote / degrade 不再作为主路线扩张。
9. P4 再接入本地模型或云端 LLM，把 LLM 能力限制在 query rewrite、tag/facet 推荐、重要记忆摘要优化、快照叙事和冲突解释。
10. P5 最后评估本地 RAG / 向量补召回；RAG 只能作为 metadata / FTS 后的语义补充。

### 15.6 P3 模块落地状态

已修改现有模块：

- `memory_records.py`：已扩 schema v2，支持 snapshot、lineage、tier、cognitive_level、facet。
- `memory_record_index.py`：已索引 schema v2 metadata / facets，并支持损坏 SQLite 索引自愈。
- `memory_compiler.py`：已扩成 runtime / snapshot / review / rollback / dao-fa-shu compiler。
- `memory_governance.py`：已保留 validate / publish / archive 治理；promote / degrade 进入 P3+。
- `memory_maintenance.py`：已支持 health / migrate / tombstone delete。
- `memory_events.py`：继续作为审计日志底层。

已新增模块：

- `memory_scoring.py`
- `memory_lineage.py`
- `memory_retrieval.py`
- `memory_record_io.py`

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

P0-1 / P0-2 计划（未实施）：

- P0-1：拆 `memory_compiler.py`（cache / render / targets / 入口）。
- P0-2：拆 `server.py`（schema / dispatch / admin / main）。
- P1-3 已完成（v0.5.2）：`CompilableRecord` 与 `compact_body` 已上提到 `memory_corpus.py`，`memory_retrieval` 不再反向依赖 compiler 私有函数。
- P1-5：把 Front Matter parser/dumper 抽到 `memory_frontmatter.py`，便于未来替换实现。

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

`v0.5.5`：多人联合治理版

- conflict / supersede / promote / degrade
- reviewer / owner / publisher 角色
- dao 层晋升约束强化
- snapshot review / governance 管理 skill

`v0.6.0`：LLM 软增强版

- query rewrite
- tag / facet 推荐
- snapshot narrative
- conflict explanation
- candidate draft

`v0.6.5`：向量补召回版

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

- 最高优先级：把 `retrieve_context` 与 `important_memories` 的预算路径对齐为严格 `budget-first`、可解释、可控尺寸的输出接口（`important_memories` 已上线）。
- 当前默认使用方式为免维护：普通用户不需要执行任何 maintenance / governance / admin 操作。
- MCP 对外接口收敛首版已完成：默认只暴露 `memory_read`、`memory_write`、`memory_context`，其余管理能力迁移到 CLI / scripts / skill，并保留 `mcp.expose_admin_tools=true` 用于 legacy/admin tools。
- P3 首版已完成：时间快照、谱系关系、importance scoring、facet、分层召回、上下文装配和 dao/fa/shu 视图已落地。
- 当前下一步是内部拆分：拆 `memory_compiler.py` / `server.py`，抽 `memory_frontmatter.py`；`memory_corpus.py` 已在 v0.5.2 完成。
- 插件内治理扩展降级为兼容方向：保留 validate / publish / archive 能力，但不再把自动审查和规则晋升作为主路线。
- P4 再做 LLM 软增强：query rewrite、tag/facet 推荐、重要记忆摘要优化、快照叙事和冲突解释。
- P5 最后做本地 RAG / 向量补召回，且只作为 metadata / FTS 后的语义补充。

### 16.1 对当前项目的直接意义

对 `ToolTest/MCP/Memory` 而言，这份设计文档的作用不是替代当前 README，而是补齐“当前版本为何存在、下一阶段要长成什么样、迁移过程中如何兼容现有调用方式”这三件事。后续如果继续开发 Memory MCP，应以本文作为 vNext 架构基线，并把 README 继续作为当前实现说明维护。
