# Final Agent Graph Runtime Architecture

## Overview

当前项目已经收敛为统一入口、双分支执行、图内审批与可重放的 Agent Graph Runtime。

顶层入口不再在 `DemoAssistantApp` 中散落业务分支逻辑，而是统一通过 `RootGraphRuntime` 进入系统。所有请求先经过显式根图语义：`goal_intake -> route_by_goal`，然后分流到 conversation 分支或 agent 分支。

## Top-Level Flow

```text
user input
  -> RootGraphRuntime
      -> goal_intake
      -> route_by_goal
          -> conversation branch
          -> agent branch
```

### Conversation Branch

```text
goal_intake
  -> route_by_goal
  -> final_response
```

特点：
- 不走 judge
- 不走 planner
- 直接生成最终闲聊回答

### Agent Branch

```text
goal_intake
  -> route_by_goal
  -> context_assembly
  -> plan_1
  -> dynamic_subgraph_1
  -> aggregate_results_1
  -> evidence_synthesis_1
  -> judge_1
      -> final_response
      -> clarification_1
      -> plan_2
```

特点：
- planner 每轮只追加局部动态子图
- judge 控制结束、澄清、继续规划
- 高风险动作可在执行中进入 `waiting_approval`
- 审批通过后回到同一图内继续执行

## Module Responsibilities

### `agent_runtime_framework/demo/app.py`
角色：薄 façade / UI 入口适配层。

负责：
- `chat`
- `stream_chat`
- `approve`
- `replay`
- context / session / memory / model center 的外部接口
- 少量 app 状态保存（如 `_pending_tokens`、`_run_history`）

### `agent_runtime_framework/workflow/routing_runtime.py`
角色：统一根图运行器。

负责：
- goal 分析
- `goal_intake`
- `route_by_goal`
- 根图 trace 注入
- 生成 `root_graph.route` / `root_graph.intent`
- 分流到 conversation 分支或 agent 分支

### `agent_runtime_framework/workflow/agent_graph_runtime.py`
角色：非闲聊任务主运行器。

负责：
- 显式固定骨架节点：
  - `goal_intake`
  - `context_assembly`
  - `plan_n`
  - `aggregate_results_n`
  - `evidence_synthesis_n`
  - `judge_n`
- 动态子图 append
- clarification / approval / final response
- loop 语义与恢复

### `agent_runtime_framework/demo/agent_branch_orchestrator.py`
角色：agent 分支 orchestration。

负责：
- 组装 `GoalEnvelope`
- 恢复 clarification 对应的 prior state / prior graph
- 调用 `AgentGraphRuntime`
- 保存 run
- 组装 payload
- 写入 run history

### `agent_runtime_framework/demo/workflow_branch_orchestrator.py`
角色：兼容图执行层。

负责：
- `compile_compat_workflow_graph()` 编译 compat graph
- 执行 compat graph
- 保留旧图拒绝与少量兼容场景支持

说明：这是明确保留的 compat 层，不是主路径。

### `agent_runtime_framework/demo/run_lifecycle.py`
角色：生命周期控制器。

负责：
- `approve`
- `replay`
- token / run 恢复
- missing token / missing run 兜底返回

### `agent_runtime_framework/demo/workflow_payload_builder.py`
角色：workflow payload presenter。

负责：
- `execution_trace`
- `evidence`
- `approval_request`
- `resume_token_id`
- `judge`
- `planned_subgraphs`
- `append_history`
- `root_graph`
- clarification status 映射

### `agent_runtime_framework/demo/workflow_run_observer.py`
角色：运行副作用同步器。

负责：
- session turn 同步
- task history 同步
- memory focus 写回

### `agent_runtime_framework/demo/runtime_factory.py`
角色：装配层。

负责统一构造：
- `RootGraphRuntime`
- `AgentGraphRuntime`
- `WorkflowRuntime`
- `AgentBranchRunner`
- `CompatWorkflowRunner`
- `RunLifecycleService`
- `WorkflowRunObserver`

## Persistence and Replay

持久化核心状态包括：
- `run.graph`
- `run.metadata.agent_graph_state`
- `run.metadata.root_graph`
- `append_history`
- pending subrun（审批恢复时）

Replay 时：
- 先从 store 读取 run
- 再通过 payload builder 还原展示 payload
- `root_graph` 会被一并恢复

## Approval Model

审批已并入 Agent Graph 执行语义：
- 高风险节点在执行期进入 `waiting_approval`
- runtime 保存 pending subrun / resume token
- `approve()` 后继续在同一图内恢复
- approval 不再是顶层旁路流程

## Remaining Compat Boundary

当前仍保留但已被明确边界化的 compat 层：
- `compile_compat_workflow_graph()`
- `CompatWorkflowRunner`
- `workspace_subtask` 对旧 backend 的适配

这些模块不再代表主路径，只用于兼容与过渡场景。

## Recommended Reading Order

1. `agent_runtime_framework/demo/app.py`
2. `agent_runtime_framework/demo/runtime_factory.py`
3. `agent_runtime_framework/workflow/routing_runtime.py`
4. `agent_runtime_framework/demo/agent_branch_orchestrator.py`
5. `agent_runtime_framework/workflow/agent_graph_runtime.py`
6. `agent_runtime_framework/demo/workflow_payload_builder.py`
7. `agent_runtime_framework/demo/run_lifecycle.py`
8. `agent_runtime_framework/demo/workflow_branch_orchestrator.py`
