# 当前 Agent 设计框架

> 最终架构说明见 `docs/architecture/final-agent-graph-runtime.md`。

> 状态说明：当前代码库的顶层主运行时已经切换为统一入口：所有请求先进入 `RootGraphRuntime`；conversation 请求走轻量 conversation graph，非 conversation 请求走 `AgentGraphRuntime`。`GraphExecutionRuntime` 是底层节点执行壳；`CodexAgentLoop` / `WorkspaceAgentLoop` 仅保留为兼容子任务后端。

## 1. 当前目标

当前 Agent 的目标不再只是“把一句用户输入映射成一个 `CodexTask` 并在单任务循环里完成”，而是：

- 把用户目标拆成显式子任务
- 用图结构表达依赖关系
- 在节点级别执行、暂停、恢复、聚合
- 用统一 final response 输出总结与引用

当前阶段已经支持的重点能力：

- 复合 workspace 请求的目标分析与拆解
- 顺序 workflow 调度
- 原生目录概览 / 文件读取节点
- 聚合节点与最终回答节点
- 节点级审批 / 恢复
- workflow 持久化恢复
- demo app 对 compound goal 的 workflow-first 路由

## 2. 顶层主链路

当前主链路可以概括为：

`用户输入 -> Goal Analysis -> (Conversation Graph | Agent Graph) -> Node Execution -> Judge/Final Response`

其中：

- 闲聊请求走 conversation graph：`final_response`
- 非闲聊 workspace 请求统一走 `AgentGraphRuntime`
- 所有请求先进入 `RootGraphRuntime`，显式经过 `goal_intake -> route_by_goal`，再分叉到 conversation 或 agent 分支
- 审批不再是顶层旁路；高风险子任务在 Agent Graph 执行过程中动态触发 `waiting_approval`，审批后继续回到图内执行
- 仅兼容场景才走 `build_workflow_graph()` / compiled workflow

需要继续清理的迁移债包括：

- `AgentGraphRuntime` 中仍然存在 direct executor calls
- 兼容 `workspace_subtask` bridge 仍有 app-owned 逻辑
- `workflow` 层对 `agents.workspace_backend` 还有反向依赖

## 2.1 当前迁移状态

当前 graph-first 迁移的边界已经明确：

- `RootGraphRuntime` 负责 route decision，但不拥有业务执行逻辑
- `AgentGraphRuntime` 负责 iterative graph orchestration，但不应该手工绕过 scheduler 调度 steady-state 节点
- `GraphExecutionRuntime` 负责 node scheduling 与 node execution
- `DemoAssistantApp` 负责路由接线与 payload 组织，不再被视为旧 loop 的直接编排器，也不应该长期持有兼容 bridge 业务逻辑
- `WorkspaceAgentLoop` / `WorkspaceBackend` 只保留为 workflow 节点下的 compatibility executor
- `workflow` 层的目标边界是不再反向依赖 `agents.workspace_backend` 的 planner-time prompt / parsing helper

| 能力区域 | 当前状态 | 目标状态 |
| --- | --- | --- |
| routing | graph-native | graph-native |
| graph build | graph-native | graph-native |
| approval / resume | graph-native | graph-native |
| aggregation | graph-native | graph-native |
| final response | graph-native | graph-native |
| complex workspace subtask execution | loop-backed compatibility | 显式 graph node 优先，loop 仅兜底 |
| clarification handling | partially loop-backed | graph-native first |
| tool-call orchestration fallback | loop-backed compatibility | 显式 graph node 优先 |

## 3. 当前核心分层

### 3.1 Entry Layer

入口仍由 `agent_runtime_framework.demo` 提供：

- `demo/server.py`
- `demo/app.py`

其中 `DemoAssistantApp` 负责：

- chat / stream / replay / approve
- conversation 与 workflow 路由
- demo payload 组织
- model center / workspace 上下文管理

`DemoAssistantApp` 不应长期负责：

- compatibility subtask result 组装
- `WorkspaceTask` / `EvidenceItem` bridge payload 生产

### 3.2 Workflow Layer

`agent_runtime_framework.workflow` 是当前顶层运行时主体。

当前已落地模块包括：

- `models.py`
- `goal_analysis.py`
- `decomposition.py`
- `graph_builder.py`
- `scheduler.py`
- `runtime.py`
- `node_executors.py`
- `codex_subtask.py`
- `aggregator.py`
- `approval.py`
- `persistence.py`

这一层负责：

- run / graph / node / result 状态表达
- ready node 调度
- 节点执行与状态推进
- approval / resume
- persistence / recovery
- 最终结果聚合

责任边界进一步收口为：

- `RootGraphRuntime` 负责 route
- `AgentGraphRuntime` 负责 orchestration
- `GraphExecutionRuntime` 负责 execution
- compatibility bridge executor 是过渡实现，不应混入 app-specific 逻辑

### 3.3 Codex Compatibility Layer

`agent_runtime_framework.agents.codex` 当前的定位是：

- 兼容已有单任务能力
- 在 workflow 节点需要时执行局部复杂子任务
- 保留 planner / evaluator / tool execution / answer synthesis 等成熟能力

换句话说：

- 以前：`CodexAgentLoop` = 主运行时
- 现在：`CodexAgentLoop` = compatibility backend

### 3.4 Infra Layer

以下基础设施仍然被复用，而不是重写：

- `tools`：工具注册与执行
- `resources`：工作区解析与语义识别
- `memory`：session / index / markdown memory
- `models`：provider / auth / route
- `policy` / `sandbox`：权限与执行约束

## 4. 当前运行模型

### 4.1 Run 级对象

当前 workflow 运行时使用以下核心对象：

- `WorkflowRun`
- `WorkflowGraph`
- `WorkflowNode`
- `WorkflowEdge`
- `NodeState`
- `NodeResult`
- `GoalSpec`
- `SubTaskSpec`

### 4.2 状态机

当前已稳定使用的 run 状态包括：

- `pending`
- `running`
- `waiting_approval`
- `completed`
- `failed`

当前已稳定使用的 node 状态包括：

- `pending`
- `running`
- `waiting_approval`
- `completed`
- `failed`

## 5. 当前生效的节点类型

已经具备最小可用实现的节点类型包括：

- `workspace_discovery`
- `content_search`
- `chunked_file_read`
- `aggregate_results`
- `evidence_synthesis`
- `verification`
- `approval_gate`
- `final_response`
- `workspace_subtask`
- `target_resolution`
- `clarification`
- `conversation_response`

其中：

- `workspace_discovery` / `content_search` / `chunked_file_read` 组成当前默认的 workspace 证据链
- `evidence_synthesis` 是当前统一的证据总结节点
- `final_response` 强制读取 judge 结果，不能绕过 `judge`
- `workspace_subtask` 当前由 `WorkspaceSubtaskExecutor` 适配旧 `WorkspaceAgentLoop` / `CodexAgentLoop` 兼容能力，并显式暴露 `fallback_reason` / `compatibility_mode` / `source_loop` 元数据

## 6. 当前已验证的主路径

本轮实现已经验证：

- workflow domain tests
- workflow runtime / scheduler tests
- decomposition / graph builder tests
- node executors / aggregator tests
- approval / persistence tests
- demo app compound-goal workflow end-to-end tests
- public surface 导出 tests

## 7. 当前仍然保留的边界

当前已经完成的 graph-first 迁移结果包括：

- `RootGraphRuntime -> AgentGraphRuntime -> GraphExecutionRuntime` 已成为 workspace 请求的主路径
- 非 conversation 的 workspace 请求统一优先进入 workflow path
- clarification follow-up 优先回到 workflow，而不是 app 层直连旧 loop
- `tool_call` / `clarification` 已成为首批显式 workflow 节点执行器
- `target_resolution` / `evidence_synthesis` 已成为稳定的 graph-native 节点执行器
- `workspace_subtask` 已收缩为 bridge executor，并显式暴露 fallback 元数据
- approval / resume / aggregation / final response 已稳定挂在 graph 结构上

当前还没有完全完成的点包括：

- 真正的并行节点执行
- 更丰富的 verification / change-flow / repair 原生节点
- model-planned graph 的进一步扩展
- subagent / MCP / skills 级别的一等图节点
- 更彻底的兼容 fallback 缩减与 dead-code cleanup
- `AgentGraphRuntime` 中 direct executor bypass 的移除
- `DemoAssistantApp` 中 compatibility bridge 逻辑的下沉
- `workflow` 对 `agents.workspace_backend` 反向依赖的清除

因此，当前框架可以描述为：

**graph-first 主路径已经成立，但仍有少量迁移期 bridge / bypass / reverse-dependency 代码需要清除；`WorkspaceAgentLoop` / `CodexAgentLoop` 仅保留为兼容 bridge backend。**
