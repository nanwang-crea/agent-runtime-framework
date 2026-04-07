# Final Agent Graph Runtime Architecture

## Overview

当前项目已经收敛为统一入口、双分支执行、图内审批与可重放的 Agent Graph Runtime。

顶层入口统一收敛在 `agent_runtime_framework.api`。`api/app.py` / `api/server.py` 只负责 FastAPI 入口与启动，`api/routes/*` 负责 HTTP/SSE 适配，`api/services/*` 直接承担各自的应用协调；所有非 HTTP 编排最终统一通过 `workflow/*` 进入系统。

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

### `agent_runtime_framework/api/app.py`
角色：FastAPI app factory / 静态资源入口。

负责：
- 组装 `FastAPI`
- 注册 `api/routes/*`
- 挂载 frontend 静态资源入口

### `agent_runtime_framework/api/routes/*`
角色：HTTP / SSE route 适配层。

负责：
- 请求校验与 HTTP 返回
- 调用 route-facing services
- 不直接承担 workflow 编排

### `agent_runtime_framework/api/services/*`
角色：小型应用服务层。

负责：
- session / context / chat / runs / model center 等 route-facing 协调
- 直接对接 workflow runtime 和持久化状态
- 不承担 HTTP 细节

当前关键文件为：
- `chat_service.py`：负责 `RootGraphRuntime`、conversation branch、agent branch、payload 组装、clarification continuation、SSE 流事件
- `run_service.py`：负责 pending token 恢复、approve、replay、缺失 token/run 的兜底 payload
- `session_service.py` / `context_service.py`：负责轻量 session / context 输出

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

### `agent_runtime_framework/workflow/nodes/`
角色：工作流节点执行器家族目录。

负责：
- 以节点家族而不是历史遗留文件名组织执行器
- `core.py` 管理聚合、验证、最终回答、审批门等核心节点
- `semantic.py` 管理 `interpret_target` / `plan_search` / `plan_read`
- `workspace_write.py` 管理 graph-native 写节点
- `discovery.py` / `interaction.py` 提供发现类与交互类节点入口
- `registry.py` 作为 `GraphExecutionRuntime` 的统一节点注册入口

说明：
- API 层不再保留 `workflow_service.py`、`agent_branch_orchestrator.py`、`workflow_branch_orchestrator.py`、`workflow_payload_builder.py`、`run_lifecycle.py`、`workflow_run_observer.py`、`pending_run_registry.py` 这类总控中间层
- 如果某条服务链路需要 runtime/persistence/payload 能力，就直接在对应 service 文件内通过私有 helper 组织

## Persistence and Replay

持久化核心状态包括：
- `run.graph`
- `run.metadata.agent_graph_state`
- `run.metadata.root_graph`
- `append_history`
- pending subrun（审批恢复时）

Replay 时：
- 先从 store 读取 run
- 再由 `run_service.py` / `chat_service.py` 内部 payload helper 还原展示 payload
- `root_graph` 会被一并恢复

## Approval Model

审批已并入 Agent Graph 执行语义：
- 高风险节点在执行期进入 `waiting_approval`
- runtime 保存 pending subrun / resume token
- `approve()` 后继续在同一图内恢复
- approval 不再是顶层旁路流程

## Graph-Native Write Path

当前写路径已经完全 graph-native：
- 文件系统请求通过 `create_path` / `move_path` / `delete_path`
- 文本编辑请求通过 `apply_patch` / `write_file` / `append_text`
- verification 继续作为独立工作流节点表达修改后的检查阶段

节点表达 workflow-stage semantics；底层 workspace tools 仍保持细粒度执行原语，不要求与节点一一对应。

## Model Requirement

当前主工作流依赖模型完成以下阶段：
- goal analysis
- decomposition
- subgraph planning
- evidence synthesis
- final response generation

这些阶段在没有可用模型时会直接报错；运行时不再提供本地 fallback 规则、fallback 总结或 fallback 最终回答。

## Recommended Reading Order

1. `agent_runtime_framework/api/app.py`
2. `agent_runtime_framework/api/services/chat_service.py`
3. `agent_runtime_framework/workflow/routing_runtime.py`
4. `agent_runtime_framework/api/services/run_service.py`
5. `agent_runtime_framework/workflow/agent_graph_runtime.py`
6. `agent_runtime_framework/api/runtime_state.py`
7. `agent_runtime_framework/api/routes/chat.py`
8. `agent_runtime_framework/api/routes/runs.py`
