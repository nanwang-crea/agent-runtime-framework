# 当前 Agent 设计框架

> 说明：本页主要描述当前仍在工作的 `assistant / capability` 主线，同时补充 2026-03-23 起新增的 `agents/codex` action-centric 方向。

## 1. 目标

当前 Agent 的目标是提供一个接近 Codex 风格的单代理桌面 AI 助手基础框架。它不是单纯的桌面文件助手，也不是仅能执行一次性 application 的运行器，而是一个具备以下能力的单代理系统：

- 维护多轮会话
- 在多个 capability 之间做选择
- 统一接入本地桌面能力、skills、MCP 能力
- 在执行前后保留可解释的决策边界

当前阶段仍然是单代理，不包含子代理调度、多代理协作和后台自治任务系统。

## 2. 当前 capability 主链路

当前 Agent 的主链路可以概括为：

`用户输入 -> AgentLoop -> plan -> capability 执行 -> review -> continue/stop -> 回复写回会话`

其中核心对象如下：

- `AssistantSession`
  负责保存当前线程中的 user / assistant turn、最近聚焦的 capability、计划历史等会话状态。

- `AssistantContext`
  负责聚合 agent 运行时依赖，包括：
  - `application_context`
  - `capabilities`
  - `skills`
  - `services`
  - `session`

- `AgentLoop`
  负责最小可行的代理循环。当前主流程为：
  1. `planner` 生成 `ExecutionPlan`
  2. 执行 `PlannedAction`
  3. `reviewer` 判断 `continue / stop`
  4. 如遇高风险 capability，则进入 `approval / resume`

- `ExecutionPlan`
- `PlannedAction`
- `ApprovalManager`
- `ApprovalRequest`
- `ResumeToken`

## 3. Capability 体系

当前 Agent 不直接把桌面动作、skill 或 MCP 当作特例，而是统一收敛到 capability 体系。

关键对象：

- `CapabilitySpec`
- `CapabilityRegistry`

`CapabilitySpec` 目前包含：

- `name`
- `runner`
- `source`
- `description`
- `safety_level`
- `input_contract`
- `cost_hint`
- `latency_hint`
- `risk_class`
- `dependency_readiness`
- `output_type`

这样 capability selector 在选择能力时，已经不再只看 capability 名称，而是可以同时参考：

- capability 的用途说明
- capability 的安全等级
- capability 的输入契约
- capability 的成本 / 延迟 / 风险
- capability 的依赖就绪度与输出类型

## 4. Skill 接入方式

`SkillRegistry` 当前已经支持 skill 元数据：

- `trigger_phrases`
- `required_capabilities`
- `planner_hint`

当前 skill 仍然是 capability 的一种来源，注册后会映射成 `skill:<name>` 形式的 capability。

这意味着当前 skill 系统已经具备两个角色：

1. 作为 fallback 触发能力
2. 作为后续 planner / selector 的语义提示源

## 5. MCP 接入方式

当前 MCP 已分两层：

- `StaticMCPProvider`
  用于测试和本地静态注册

- `MCPClientAdapter`
  用于把具备 `list_tools()` / `call_tool()` 接口的 MCP client 映射为框架内的 provider

MCP provider 暴露的是 `MCPToolSpec`，其中包含：

- `name`
- `description`
- `input_schema`
- `safety_level`
- `cost_hint`
- `latency_hint`
- `risk_class`
- `dependency_readiness`
- `output_type`
- `runner`

这使得 MCP 工具已经具备“可发现 schema”能力，而不是只能以静态回调形式存在。

## 6. 模型接入层设计

当前框架已经开始使用 LLM，但还没有独立的“模型接入层”。后续为了支持：

- 用户选择使用哪个模型
- 同时接入多个 provider
- 用户为不同 provider 做 auth 登录
- conversation / planner / selector / summarizer 使用不同模型

需要把模型能力从 `ApplicationContext.llm_client` 这种单点字段，提升为一个单独子系统。

建议新增一个 `models/` 或 `providers/llm/` 模块，核心对象包括：

- `ModelProvider`
- `ModelRegistry`
- `ModelProfile`
- `AuthSession`
- `CredentialStore`
- `ModelRouter`

职责划分建议如下：

- `ModelProvider`
  负责对接具体厂商，如 OpenAI、Anthropic、Gemini、OpenRouter 或本地模型网关。

- `ModelRegistry`
  负责注册所有可用 provider 与模型清单，并暴露统一查询接口。

- `ModelProfile`
  负责描述单个模型的语义属性，例如：
  - `provider`
  - `model_name`
  - `display_name`
  - `context_window`
  - `supports_tools`
  - `supports_vision`
  - `cost_level`
  - `latency_level`
  - `reasoning_level`
  - `auth_requirement`

- `AuthSession`
  负责描述用户与某个 provider 的登录状态，而不是把 API key 直接散落在 application config 里。

- `CredentialStore`
  负责安全存储 provider 凭据。桌面端阶段建议优先对接系统 keychain，而不是明文配置。

- `ModelRouter`
  负责把不同子任务分配给不同模型，例如：
  - 对话走 conversation model
  - capability selector 走 cheap-fast model
  - planner / reviewer 走 stronger reasoning model
  - summarize 走 long-context model

这层的关键点不是“多接几个模型”，而是让模型成为框架中的一级资源，而不是一个散落在各处的 SDK client。

## 7. 当前桌面能力如何挂进来

当前桌面内容能力仍由 `desktop_content_application` 承担，但它现在只是 capability 体系中的一个实现，而不再是整个系统的顶层入口。

桌面能力内部结构：

- `ResolverPipeline`
- `DesktopActionHandlerRegistry`
- `run_stage_parser`

当前 Agent 会把它作为 `desktop_content` capability 注册进 `CapabilityRegistry`，然后由 `AgentLoop` 选择是否调用。

## 8. 当前设计的边界

当前 Agent 框架已经具备：

- 单代理主循环
- 最小 `plan -> act -> review -> continue/stop` 循环
- 多 capability 统一编排
- skills 插槽
- MCP 插槽
- 桌面内容 capability
- conversation capability
- LLM-first capability selector
- approval / resume 骨架

但仍然没有：

- 子代理委派
- 统一 artifact 系统
- 更强的 planner / reviewer 结构化 LLM 版本
- 会话级 MCP 生命周期治理
- 独立的模型注册 / 认证 / 路由层

因此它现在属于“单代理平台骨架已成型，已具备最小代理循环与审批恢复能力，但模型基础设施与深度智能编排仍偏薄”的阶段。

## 9. 新增的双层方向

从 2026-03-23 开始，框架不再把“Codex 风格 agent”与“通用 assistant framework”混在同一条主链路里，而是改成双层结构：

- `Kernel`
  通用底座：graph、policy、models、tools、artifacts、memory、approval、checkpoint
- `Profile / Runtime`
  当前 `assistant` 运行时仍然存在，继续承担 capability-centric 的桌面助手装配
- `Agent`
  新增 `agents/codex`，负责 action-centric 的 Codex 风格强执行闭环

这意味着当前仓库里已经存在两条并行路线：

1. `assistant`：
   面向桌面助手、capability 选择、conversation + desktop content 组合
2. `agents/codex`：
   面向任务推进、工具调用、审批恢复、artifact 记录、验证闭环

## 10. 当前 Codex Runtime 的最小能力

新增 `agents/codex` 目前已经具备：

- `CodexTask`
- `CodexAction`
- `CodexActionResult`
- `VerificationResult`
- `CodexAgentLoop`

当前最小内置动作 / 工具闭环已经开始形成：

- `call_tool`
- `run_verification`
- `apply_patch`
- `move_path`
- `delete_path`
- `create_path`
- `edit_text`
- 默认 tool 集：
  - `list_workspace_directory`
  - `read_workspace_text`
  - `summarize_workspace_text`
  - `run_shell_command`
  - `apply_text_patch`
  - `move_workspace_path`
  - `delete_workspace_path`
  - `create_workspace_path`
  - `edit_workspace_text`

并且已经具备：

- LLM-first next-action planner
- tool schema + task state + recent observation 参与下一步动作选择
- 默认启发式 planner
- artifact 持久化
- 高风险 action 的 approval / resume
- demo 桌面端入口已经切换为 `CodexAgentLoop`
- demo / frontend shell 已具备同会话切换 agent 与 workspace 的 context 骨架

## 11. 当前阶段的架构判断

当前方向比之前更正确，原因不是“抽象更多”，而是“抽象位置更低”：

- 以前更偏 `capability-centric`
- 现在已经开始形成 `action/tool-centric`
- 写操作的风险语义已经开始从 application/capability 层下沉到 action 层
- planner 的决策输入已经开始包含 tool schema、task state 和 recent observation

这更接近 Codex 类型 agent 的真实工作方式。

但当前仍然只是第一阶段，主要限制仍然是：

- 默认 planner 还是启发式规则，不是真正的 task planner
- `desktop_content_application` 仍作为兼容层保留，还没有被彻底移除
- frontend 还只是第一阶段 shell，还没有形成真正的 profile/plugin 式面板体系
- command / patch 目前还是最小版本，不是完整工作区执行系统
