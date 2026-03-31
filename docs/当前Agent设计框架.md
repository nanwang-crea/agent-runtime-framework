# 当前 Agent 设计框架

> 状态说明：当前代码库的顶层主运行时已经切换为 `workflow-first`。实际生效的主链是 `demo/app.py -> WorkflowRuntime -> workflow/*`，`CodexAgentLoop` 继续保留，但定位为兼容子任务执行后端，而不是顶层唯一运行时。

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

`用户输入 -> 路由判断 -> Goal Analysis -> Goal Decomposition -> Graph Build -> Workflow Runtime -> Node Execution -> Aggregation -> Final Response`

其中：

- 普通对话请求仍可走 conversation path
- 复合 workspace 请求优先走 workflow path
- 单个复杂子任务可下沉到 `CodexSubtaskExecutor -> CodexAgentLoop`

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

### 3.2 Workflow Layer

`agent_runtime_framework.workflow` 是当前顶层主运行时。

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

- `repository_explainer`
- `file_reader`
- `aggregate_results`
- `final_response`
- `codex_subtask`

其中：

- `repository_explainer` 当前由 `WorkspaceOverviewExecutor` 实现
- `file_reader` 当前由 `FileReadExecutor` 实现
- `aggregate_results` 当前由 `AggregationExecutor` 实现
- `final_response` 当前由 `FinalResponseExecutor` 实现
- `codex_subtask` 当前由 `CodexSubtaskExecutor` 适配旧 `CodexAgentLoop`

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

当前还没有完全完成的点包括：

- 真正的并行节点执行
- 更丰富的 verification / change-flow 原生节点
- workflow-first 覆盖更多 demo / app 路径
- 更彻底的 codex-only 旧路径降级与清理
- 全量回归里旧 `CodexAgentLoop` 修复链路的 3 个失败用例收口

因此，当前框架可以描述为：

**workflow 已成为顶层主运行时，但 Codex compatibility backend 仍在过渡期。**
