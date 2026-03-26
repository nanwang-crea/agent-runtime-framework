# Agent Runtime Framework 与 OpenCode Agent 能力对照表

## 目的

这份文档用于对比当前 `agent-runtime-framework` 中自研 Agent 与 `opencode` 中 Agent 的实现方式和关键能力差异。

重点不是比较谁的代码更复杂，而是分析：

- 两者分别把 Agent 的“智力”放在哪些层实现
- 哪些能力会直接影响任务理解、稳定性和执行体验
- 当前自研 Agent 和 `opencode` 相比，短板主要在哪里
- 哪些 `opencode` 的机制最值得借鉴

---

## 一句话结论

`agent-runtime-framework` 更像一个强调任务语义、resource semantics 和 planner 闭环的 Agent 内核。

`opencode` 更像一个成熟的 coding agent 运行时，重点不在“单个 planner 多聪明”，而在于：

- agent 画像清晰
- prompt 组装完整
- 工具运行时成熟
- session 主循环稳定
- 子代理是独立 session
- instruction 继承能力强

因此，`opencode` 给人的体感通常是：

- 不一定在“显式规划”上更学术
- 但在真实 coding 任务中更稳、更像一个完整的工作系统

---

## 总体对照表

| 维度 | agent-runtime-framework | opencode | 对智力体感的影响 |
| --- | --- | --- | --- |
| 总体架构 | 单 Agent 骨架更明显，强调 planner / evaluator / resource semantics | 多层运行时架构，Agent / Session / Prompt / LLM / ToolRegistry 分层清晰 | `opencode` 更像成熟产品，执行稳定性更强 |
| Agent 定义方式 | 更偏任务理解和能力抽象 | 更偏“prompt + permission + mode + model”的 agent 画像 | `opencode` 更容易通过 agent 切换稳定控制行为 |
| 主循环 | 更强调 plan -> act -> review | 更强调 session loop + tool calling + processor 驱动 | `opencode` 在真实会话中更顺滑、更像产品级 agent |
| Prompt 结构 | 多个局部 prompt | 多层 prompt pipeline：provider prompt + environment + skills + instructions + user/system override | `opencode` 上下文组织更成熟 |
| 本地规则注入 | 有方向，但还偏轻 | 自动发现并加载 `AGENTS.md` / `CLAUDE.md` / 配置 instructions / URL instructions | `opencode` 更容易“理解这个仓库怎么协作” |
| 工具运行时 | 已有 registry / capability / MCP 基础 | 工具注册、参数校验、输出截断、tool repair、plugin hooks 都很成熟 | `opencode` 工具调用更稳，失败更少显得笨 |
| 子代理 | 当前仍更偏单代理系统 | `task` 工具直接创建独立子 session 跑 subagent | `opencode` 更擅长拆分复杂任务 |
| 权限系统 | 有 policy / approval 设计 | agent 天然带 permission ruleset，session 还能叠加权限 | `opencode` 更容易用“权限画像”塑造 agent 行为 |
| 历史与会话组织 | 有 session/memory，但整体运行时会话编排较轻 | session、message、processor、compaction、summary 形成完整闭环 | `opencode` 的多轮任务连续性更强 |
| 容错与恢复 | 已有 retriable / recovery 思路 | 对 tool call repair、compat、resumption、structured output 等做了运行时兜底 | `opencode` 用户体感更稳，不容易一处失败就崩掉 |

---

## 分项细化对照

### 1. 总体实现哲学

| 对比项 | agent-runtime-framework | opencode |
| --- | --- | --- |
| 核心关注点 | 任务语义、planner、resource semantics、恢复链路 | session 运行时、prompt 组装、agent 画像、工具执行稳定性 |
| 设计气质 | 偏 Agent 内核 / cognitive architecture | 偏产品化 coding agent runtime |
| 智力来源 | 更依赖 task understanding 和显式流程 | 更依赖运行时编排和 prompt/tool/session 体系 |

影响：

- 你的框架更像“在研究 agent 应该怎么理解任务”
- `opencode` 更像“在研究 agent 如何稳定完成任务”

结论：

- 两者不是谁绝对更先进，而是发力点不同
- 当前如果要快速提升体感，借鉴 `opencode` 的运行时工程能力会非常有效

---

### 2. Agent 的定义方式

| 对比项 | agent-runtime-framework | opencode |
| --- | --- | --- |
| Agent 的核心身份 | 由任务类型、planner、capability、resource semantics 驱动 | 由 `name + description + mode + permission + prompt + model + steps` 定义 |
| 角色切换方式 | 更偏同一 agent 在不同任务下走不同路径 | 通过 build / plan / general / explore 等不同 agent 画像切换 |
| 行为边界 | 边界更多藏在 planner / policy 中 | 边界更多体现在 permission ruleset 和 mode 上 |

影响：

- `opencode` 很擅长让模型“先在一个清晰角色里工作”
- 你的系统更容易在一个 agent 内部承担过多职责

结论：

- 如果一个 Agent 既负责探索、又负责规划、又负责修改、又负责总结，就更容易显得忽聪明忽笨
- `opencode` 的一个重要经验是：用多个权限画像明确切开职责

---

### 3. 主循环与执行模型

| 对比项 | agent-runtime-framework | opencode |
| --- | --- | --- |
| loop 风格 | 更显式的 plan -> act -> review 风格 | while-loop 会话驱动，围绕 tool calling 和 processor 前进 |
| 控制点 | planner、evaluator、runtime 状态 | session、message、processor、compaction、status |
| 终止方式 | evaluator / pending_verification / action state | finish reason、steps 限制、structured output、compaction |

影响：

- 你的 loop 更像研究型 Agent loop
- `opencode` 的 loop 更贴近实际产品交互和 streaming tool-calling

结论：

- 你现在缺的不是 loop，而是“围绕 loop 的运行时配套层”

---

### 4. Prompt 组装方式

| 对比项 | agent-runtime-framework | opencode |
| --- | --- | --- |
| prompt 组织 | 多个局部 prompt，各管一段逻辑 | provider prompt + environment + skills + instructions + user override |
| prompt 入口 | 更偏 planner/evaluator 专用提示 | 更偏 runtime 统一注入、按 session 动态拼装 |
| prompt 灵活性 | 有，但还不够系统化 | 很强，不同模型、不同 agent、不同 session 均可动态调整 |

影响：

- `opencode` 模型一开局就更容易知道：自己是谁、在哪、能用什么、当前应遵守哪些项目规则

结论：

- 你当前需要的不是继续零散补 prompt，而是建立 prompt assembly pipeline

---

### 5. Instruction 与项目规则继承

| 对比项 | agent-runtime-framework | opencode |
| --- | --- | --- |
| 全局规则加载 | 有基础设计，但相对轻 | 可自动加载全局/项目/局部 `AGENTS.md`、`CLAUDE.md`、配置 instructions |
| 文件级局部规则 | 暂不明显 | 读取文件时会向上查找该路径附近的 instruction 文件 |
| 扩展来源 | 主要依赖代码内显式逻辑 | 文件、配置、URL 均可作为 instructions 来源 |

影响：

- `opencode` 在代码仓任务里更容易显得“很懂这个目录的本地规则”

结论：

- 这是 `opencode` 最值得借鉴的能力之一
- 局部 instruction 继承会显著提升代码库理解能力

---

### 6. 工具层设计

| 对比项 | agent-runtime-framework | opencode |
| --- | --- | --- |
| 工具抽象 | registry / capabilities / MCP 基础已具备 | `Tool.define` + `ToolRegistry` + plugin / MCP / truncation / validation 形成完整体系 |
| 参数校验 | 有，但整体执行体验还可以继续打磨 | 每个工具统一 zod 校验，错误信息明确要求模型重写输入 |
| 输出处理 | 有基础能力 | 自动截断长输出，并携带 truncation metadata |
| 模型适配 | 较轻 | 根据模型选择 `apply_patch` 或 `edit/write` 等工具形式 |

影响：

- `opencode` 更会“帮模型把工具调用纠正回来”
- 这会明显减少工具出错时的笨感

结论：

- 你的工具架构方向不差，但还少一层统一的运行时包装器

---

### 7. Tool call 修复与运行时兜底

| 对比项 | agent-runtime-framework | opencode |
| --- | --- | --- |
| tool call repair | 较少 | 对错误大小写工具名可自动修复，否则回退到 `invalid` 工具 |
| provider 兼容性处理 | 暂不明显 | 对 LiteLLM、OpenAI OAuth、GitLab workflow 等做兼容层 |
| structured output | 还不是主路径 | 内置 `StructuredOutput` 工具，严格要求模型用 tool 返回结构化结果 |

影响：

- 很多“模型笨”其实是 runtime 没兜住
- `opencode` 在这方面做得更成熟，所以用户感觉它更稳定

结论：

- 想提升体感，运行时 repair 和 fallback 很重要

---

### 8. 子代理与任务拆分

| 对比项 | agent-runtime-framework | opencode |
| --- | --- | --- |
| 子代理机制 | 当前仍偏单代理架构 | `task` 工具创建独立子 session 来跑 subagent |
| 隔离方式 | 更多靠内存和流程隔离 | 会话级隔离，支持恢复、权限控制、父子关系 |
| 子任务结果返回 | 需要继续设计 | 直接返回 `task_id` 和结果，便于续跑 |

影响：

- `opencode` 不需要把所有复杂问题都塞进一个主 loop 里
- 复杂任务的认知负载可以被分流

结论：

- 如果你想让系统处理更复杂任务，subagent 最好做成独立 session，而不是普通函数调用

---

### 9. 权限与行为边界

| 对比项 | agent-runtime-framework | opencode |
| --- | --- | --- |
| 权限模型 | 有 desktop policy、approval、safe/destructive write 概念 | agent permission ruleset + session permission merge + tool ask |
| 行为边界表达 | 偏 policy decision | 偏“哪些工具/路径/动作 allow、ask、deny” |
| agent 画像塑造 | 主要靠 planner / policy | 主要靠 permission + prompt + mode |

影响：

- `opencode` 更容易把“不同 agent 的性格和能力边界”稳定下来

结论：

- 你的 policy 设计是对的，但还可以更进一步让它参与 agent 画像构建

---

### 10. Session、历史与连续性

| 对比项 | agent-runtime-framework | opencode |
| --- | --- | --- |
| 会话系统 | 有 session memory / working memory / index memory | session / message / processor / status / summary / compaction 很完整 |
| 长上下文处理 | 还在建设中 | 内置 compaction agent、summary agent |
| 多轮工作体验 | 基础已具备 | 更强，尤其适合长对话 coding session |

影响：

- `opencode` 在长 session 中更像一个持续工作的 coding partner

结论：

- 这也是它“产品感”比当前自研框架更强的重要原因之一

---

## 最值得借鉴的 8 个点

结合实现细节，`opencode` 最值得借鉴的不是某个单点算法，而是下面这些机制：

1. 用 `Agent = prompt + permission + mode + model + steps` 明确 agent 画像
2. 用统一的 session loop 驱动整个 Agent，而不是把控制逻辑分散到太多局部模块
3. 建立 prompt assembly pipeline，而不是散落 prompt
4. 自动发现并继承 `AGENTS.md` / `CLAUDE.md` / 配置 instructions
5. 给工具加统一执行包装：参数校验、输出截断、格式化错误
6. 增加 tool call repair 和 provider compatibility 层
7. 把 subagent 做成独立 session，而不是普通内部调用
8. 用 compaction / summary / status 把长会话运行时补齐

---

## 当前自研 Agent 的优势

虽然 `opencode` 的产品成熟度更高，但你的框架并不是全面落后。

### 1. 任务语义建模方向更清晰

你的系统更明确地在追求：

- task profile
- target semantics
- resource semantics
- evidence sufficiency
- recovery strategy

这些内容在认知结构上其实很强。

### 2. planner / evaluator 思路更显式

`opencode` 更像成熟 runtime，很多“理解能力”是系统整体协同出来的。

你的框架则更容易明确知道：

- 为什么进入某条路径
- 为什么补一轮 inspect
- 为什么当前证据不足

这对后续做更强 Agent 很有价值。

### 3. resource semantics 是很有潜力的长板

如果你把 resource semantics 真正做成全链路主导输入，它未来在目录理解、follow-up 解析、workspace 操作上的上限可能非常高。

---

## 最终判断

如果把两者差异说得最直白一些：

- `agent-runtime-framework` 当前更像“懂 Agent 原理的内核原型”
- `opencode` 当前更像“围绕 coding workflow 打磨出的生产级 Agent runtime”

所以，当前如果你的目标是：

- 快速提升用户体感中的“聪明”和“稳定”

那么最值得优先借鉴 `opencode` 的，不是它某段 prompt 文案，而是它这套运行时结构：

- agent 画像
- prompt 组装
- instruction 继承
- tool runtime 包装
- session loop
- subagent session 化
- compaction / summary / status

这些机制一旦补上，你自己的框架会非常明显地更像一个成熟产品级 Agent。
