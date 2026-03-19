# Chat UI And Streaming Experience Design

**Date:** 2026-03-18

**Goal:** 把当前 desktop assistant demo 从“基础聊天页”升级成“更接近 Codex/Cursor 的 agent 工作台”，支持正文流式、过程流嵌入聊天、结构化错误展示、Markdown 渲染和上下文可视化。

## 1. Requirement Summary

当前已确认的需求如下：

1. 聊天界面需要整体优化，信息层级更清晰，视觉更成熟，适合长时间使用。
2. 模型输出的最终回答需要继续保持流式展示。
3. 中间过程也需要可见，但不应该混入正文；应像 Codex 一样在聊天消息内部实时打印过程，结束后自动折叠。
4. 流式失败、资源定位失败、目录/文件类型错误等异常需要在前端可见，不能只打印在后端终端。
5. Assistant 正文需要支持 Markdown 渲染，包括标题、列表、引用、代码块、表格和链接。
6. 会话中形成的上下文、焦点资源和摘要需要以 UI 方式呈现，而不是只存在后端内存中。
7. 非对话类任务也应该有“过程感”，避免用户在长时间读取/总结文件时只能等待最终结果。

## 2. Product Direction

本次设计采用“单消息双层输出”模型，而不是把过程日志放到独立侧栏：

1. **正文层**
   assistant 最终回答内容，按 Markdown 实时流式渲染。

2. **过程层**
   运行状态、步骤、warning、error 等中间事件，嵌入在同一条 assistant 消息的运行块中。

这样做的原因：

- 用户视线不需要在聊天区和侧栏日志之间反复切换。
- 中间过程和最终结果属于同一轮回答，语义上更连贯。
- 任务完成后自动折叠过程块，历史消息仍然干净。
- 错误可以自然地出现在回答上下文中，而不是以系统级弹窗或静默失败呈现。

## 3. Interaction Model

每一轮 assistant 输出由三部分组成：

### 3.1 Run Header

显示这轮任务的高层状态：

- 当前 capability，例如 `conversation`、`desktop_content`
- 当前状态，例如 `running`、`completed`、`warning`、`error`
- 当前阶段描述，例如 `正在读取 README.md`

Run Header 始终可见，是用户判断系统是否仍在工作的核心反馈。

### 3.2 Run Block

运行中默认展开，逐条打印过程事件。示例：

- 正在选择能力
- 已选择 `desktop_content`
- 正在解析资源
- 已定位 `README.md`
- 正在生成总结
- 流式失败，已退回非流式

完成后自动折叠，只保留一行摘要，例如：

- 已读取 `README.md` 并生成总结
- 已定位 `docs` 并给出下一步建议

失败时不折叠，保持展开，并在底部显示错误卡片。

### 3.3 Answer Body

正文区只显示 assistant 的最终自然语言输出，按 Markdown 实时渲染。  
中间步骤不进入正文，避免用户把过程日志误读为答案内容。

## 4. Screen Layout

建议整体布局改成“两栏主视图”：

### 4.1 Left / Main Column

主聊天区，包含：

- 用户消息
- assistant 运行消息
- Markdown 正文
- 错误卡片
- 建议动作按钮

### 4.2 Right / Context Column

右侧不再承担主过程日志展示，只保留上下文和状态信息：

- Focused Resource
- Recent Resources
- Working Summary
- Active Capability
- Model Routes

右侧的作用是“assistant 当前理解的可视化”，不是“运行日志主界面”。

## 5. Streaming Model

本次设计明确区分两类流：

### 5.1 Content Stream

用于最终正文输出，进入 Answer Body。

- conversation 真实 token/chunk
- summarize/read 等任务的最终文本结果
- 始终按 assistant 正文处理

### 5.2 Process Stream

用于过程事件输出，进入 Run Block。

事件类型包括：

- `status`
- `step`
- `warning`
- `error`
- `memory`

设计原则：

- 过程流永远不直接写进正文
- 过程流是可折叠的运行记录
- 过程结束后只保留摘要，必要时用户可展开查看

## 6. Event Contract

前后端事件模型建议固定为：

- `start`
- `status`
- `step`
- `delta`
- `memory`
- `warning`
- `error`
- `final`

### 6.1 `start`

请求开始，初始化 run message。

### 6.2 `status`

高层过程描述，用于：

- Run Header 当前状态
- Run Block 过程日志中的轻量条目

### 6.3 `step`

结构化步骤状态变化，用于更明确的过程节点展示。

### 6.4 `delta`

正文增量。只进入 Markdown Answer Body。

### 6.5 `memory`

更新右侧 Context 面板，包括：

- focused resource
- recent resources
- working summary
- active capability

### 6.6 `warning`

可恢复问题，例如：

- 流式失败，已退回非流式
- 定位结果不精确，已使用默认目录

### 6.7 `error`

结构化错误，终止本轮请求。错误必须对用户可见，不允许只写终端日志。

### 6.8 `final`

请求成功结束，带完整 payload。

## 7. Error Handling Design

错误展示必须遵循“业务化而不是裸异常”的原则。

统一错误对象字段：

- `code`
- `message`
- `detail`
- `stage`
- `retriable`
- `suggestion`

典型错误码：

- `RESOURCE_NOT_FOUND`
- `RESOURCE_IS_DIRECTORY`
- `RESOURCE_NOT_DIRECTORY`
- `RESOURCE_OUTSIDE_WORKSPACE`
- `MODEL_UNAVAILABLE`
- `MODEL_REQUEST_FAILED`
- `MODEL_STREAM_FALLBACK`
- `STREAM_BROKEN`
- `INTERNAL_ERROR`

在 UI 中，错误以聊天气泡中的 Error Card 呈现：

- 标题：错误 message
- 副信息：code + stage
- 建议：suggestion
- 后续可扩展按钮：
  - 重试
  - 列出目录
  - 读取 README

## 8. Markdown Rendering Design

Assistant 正文必须支持 Markdown 渲染，第一版支持：

- 标题
- 段落
- 有序列表/无序列表
- 引用
- 行内代码
- 代码块
- 表格
- 链接
- 分隔线

渲染规则：

- assistant 内容按 Markdown 渲染
- user 内容保持原样文本
- 流式过程中允许重新渲染 Markdown
- 错误卡片、状态日志不使用 Markdown 正文渲染

代码块的视觉要求：

- 等宽字体
- 独立背景
- 更强的边界感
- 语言标签可见

## 9. Memory And Context Design

当前后端已有 session memory，可直接映射到前端：

- `focused_resource`
- `recent_resources`
- `last_summary`
- `active_capability`

前端 Context 面板以“assistant 当前理解”为主题展示，而非暴露原始内部对象。

建议文案：

- Focused Resource
- Recent Context
- Working Summary
- Active Capability

目标是让用户理解 assistant 当前围绕什么资源工作、形成了什么临时结论，而不是看到技术字段堆叠。

## 10. Visual Design Direction

视觉目标不是“大幅重绘”，而是让页面更像工作台：

- 主聊天区优先内容可读性
- Run Block 使用更轻的面板样式，不抢正文注意力
- Markdown 正文宽度控制在舒适阅读范围
- 代码块、引用、列表与普通段落拉开层次
- 状态颜色语义清晰：
  - running：绿色或强调色
  - warning：琥珀色
  - error：柔和红色
- 完成后折叠的 Run Block 视觉上应更轻，避免刷屏

总体风格关键词：

- 稳定
- 清晰
- 工具化
- 可读
- 长时使用不疲劳

## 11. Implementation Scope

### Phase 1: Core Experience Upgrade

目标：先把体验骨架立起来。

- 聊天区引入 Run Header + Run Block + Answer Body
- 过程流嵌入聊天消息中
- 完成后自动折叠
- 错误卡片接入聊天流
- Markdown 渲染接入
- 右侧保留 Context，而不是主过程日志

### Phase 2: Richer Process Streaming

目标：让非 conversation 任务也更像 agent 工作流。

- 补足 `routing / resolve / execute / compose / remember` 过程事件
- warning 事件细化
- 生成更好的折叠摘要

### Phase 3: Polishing

目标：提高完成度。

- 代码块增强
- 建议动作按钮
- 视觉细节和动效
- 更好的历史消息回放体验

## 12. Acceptance Criteria

以下标准满足时，认为本轮设计落地完成：

1. assistant 消息中可以看到运行过程，并且过程结束后默认折叠。
2. 中间过程不会混入正文内容。
3. 正文支持 Markdown 渲染，代码块和列表可正常显示。
4. 目录总结、资源未命中、模型异常等错误可在前端看到结构化提示。
5. 用户在等待长任务时，能持续看到过程状态变化，而不是空等。
6. 右侧 Context 能正确展示当前焦点资源和工作摘要。

## 13. Out Of Scope

本轮不做以下内容：

- 完整对标 Cursor 的多线程会话管理
- 工具调用树视图
- 复杂 diff 预览
- 多消息并发运行
- 富文本编辑器级别的 Markdown 输入能力

这些能力后续可以在当前设计基础上逐步扩展。
