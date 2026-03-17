# 当前 Agent 设计框架

## 1. 目标

当前 Agent 的目标是提供一个接近 Codex 风格的单代理桌面 AI 助手基础框架。它不是单纯的桌面文件助手，也不是仅能执行一次性 application 的运行器，而是一个具备以下能力的单代理系统：

- 维护多轮会话
- 在多个 capability 之间做选择
- 统一接入本地桌面能力、skills、MCP 能力
- 在执行前后保留可解释的决策边界

当前阶段仍然是单代理，不包含子代理调度、多代理协作和后台自治任务系统。

## 2. 当前主链路

当前 Agent 的主链路可以概括为：

`用户输入 -> AgentLoop -> capability 选择 -> capability 执行 -> 回复写回会话`

其中核心对象如下：

- `AssistantSession`
  负责保存当前线程中的 user / assistant turn、最近聚焦的 capability 等会话状态。

- `AssistantContext`
  负责聚合 agent 运行时依赖，包括：
  - `application_context`
  - `capabilities`
  - `skills`
  - `services`
  - `session`

- `AgentLoop`
  负责单轮代理决策与执行。当前选择顺序为：
  1. 显式 `capability_selector` override
  2. LLM-first 结构化 capability 选择
  3. triggered-skill fallback
  4. 默认 desktop capability / 首个 capability

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

这样 capability selector 在选择能力时，已经不再只看 capability 名称，而是可以同时参考：

- capability 的用途说明
- capability 的安全等级
- capability 的输入契约

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
- `runner`

这使得 MCP 工具已经具备“可发现 schema”能力，而不是只能以静态回调形式存在。

## 6. 当前桌面能力如何挂进来

当前桌面内容能力仍由 `desktop_content_application` 承担，但它现在只是 capability 体系中的一个实现，而不再是整个系统的顶层入口。

桌面能力内部结构：

- `ResolverPipeline`
- `DesktopActionHandlerRegistry`
- `run_stage_parser`

当前 Agent 会把它作为 `desktop_content` capability 注册进 `CapabilityRegistry`，然后由 `AgentLoop` 选择是否调用。

## 7. 当前设计的边界

当前 Agent 框架已经具备：

- 单代理主循环
- 多 capability 统一编排
- skills 插槽
- MCP 插槽
- 桌面内容 capability
- LLM-first capability selector

但仍然没有：

- 长链路规划器
- 多步自反思
- 子代理委派
- 会话级 approval / resume 编排
- 统一 artifact 系统

因此它现在属于“单代理平台骨架已成型，但智能编排层仍较薄”的阶段。
