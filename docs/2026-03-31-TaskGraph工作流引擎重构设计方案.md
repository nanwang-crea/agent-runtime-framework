# Task Graph / Workflow Engine 重构设计方案

> 状态说明：本文是面向下一阶段的大重构设计稿，目标是把当前 `demo/app.py -> CodexAgentLoop -> planner/evaluator/tools` 的单任务执行链，升级为可处理复合目标、并行子任务、审批恢复和长链状态持久化的图驱动工作流引擎。重要提醒：可以大修大改，删除无用的模块

## 进展更新（2026-03-31）

当前代码已完成一个最小可用 workflow 纵向切片：

- 已落地 `workflow` 域模型、scheduler、runtime、approval、persistence
- 已落地 goal analysis / decomposition / graph builder
- 已落地原生 `workspace_overview`、`file_read`、aggregation、final response
- 已落地 `CodexSubtaskExecutor` 兼容层
- demo app 已对 compound goal 走 workflow-first 路径
- 顶层公开面已导出 `WorkflowRuntime` / `WorkflowRun` 等对象

当前文档中关于“未来要新增”的这些部分，应理解为：**方向已被验证，部分已经实现，但仍未完全完成全量迁移与清理。**

## 1. 设计目标

本次重构不再以“继续增强单个 Codex loop”作为主方向，而是直接把 Agent 主运行时升级为：

`用户目标 -> Goal Analysis -> Task Graph Build -> Workflow Runtime -> Node Execution -> Aggregation -> Final Response`

目标能力：

- 稳定处理复合请求，例如：
  - 列目录 + 读 README + 总结
  - 改多个文件 + 跑验证 + 汇总 diff
  - 查信息 + 生成结论 + 给出引用
- 支持显式子任务分解，而不是把一句话压成单个 `task_profile`
- 支持节点级暂停 / 恢复 / 审批 / 重试
- 支持串行与并行节点执行
- 支持 run 级共享记忆、证据、引用与 artifact 汇总
- 为后续多代理 / specialist node / MCP node 预留结构

非目标：

- 本阶段不追求一次性引入真正的多 agent 协作协议
- 不重写现有工具层、资源层、记忆层的全部实现
- 不要求第一版就支持动态自修改 graph

---

## 2. 当前设计的根问题

当前主链虽然已经具备较强的单任务能力，但核心问题在于：

- 一次用户请求通常只会映射为一个 `TaskIntent.task_kind`
- 一次运行通常只会对应一个 `CodexTask`
- 一个 `CodexTask` 只会生成一条 plan
- evaluator 判断的是“当前任务是否完成”，不是“用户所有子目标是否都完成”

这带来三个结构性问题：

### 2.1 无法稳定处理复合请求

例如：

- “帮我列一下当前文件夹都有什么，以及读取一下 README 文件并总结告诉我在讲什么”

当前系统大概率只能把它判成：

- `repository_explainer`

或：

- `file_reader`

而不是显式拆成：

1. 当前目录概览
2. README 阅读总结
3. 最终汇总回答

### 2.2 完成标准偏单任务

当前 evaluator / planner 主要围绕：

- 这个 task 的证据是否够
- 要不要继续下一步 action

但缺少：

- 所有子目标是否都完成
- 哪个子目标尚未完成
- 是否还差最终聚合 / 引用 / 总结

### 2.3 恢复粒度不够细

虽然当前已有 approval / resume / persisted clarification，但恢复单位主要还是“整个 task”，而不是“workflow graph 中某个节点”。

这使得后续要做：

- 并行节点
- 长链恢复
- specialist node
- 节点级重试

时会越来越吃力。

---

## 3. 重构后的总体架构

### 3.1 总体分层

新的主架构建议分成六层：

1. **Entry Layer**
   - 负责 HTTP / stream / replay / approve / context switch
   - 不再直接驱动 `CodexAgentLoop`

2. **Workflow Orchestrator Layer**
   - 新的唯一主运行时
   - 负责 run lifecycle、graph 调度、恢复、并行控制、全局完成判断

3. **Goal Analysis & Graph Build Layer**
   - 负责把用户请求转成结构化 goal
   - 负责把 goal 编译成 task graph / workflow DAG

4. **Node Execution Layer**
   - 每个 graph node 负责一件明确事情
   - 可复用现有 planner/evaluator/tools/codex loop 能力

5. **Tool Runtime Layer**
   - 保留当前 ToolRegistry / execute_tool_call / sandbox / constraints

6. **Infra Layer**
   - resources / memory / models / observability / artifacts

### 3.2 新的主调用链

重构后，主调用链建议变为：

`DemoAssistantApp -> WorkflowRuntime.run() -> GoalAnalyzer -> GraphBuilder -> Scheduler -> NodeExecutor -> Aggregator -> FinalResponse`

而不是：

`DemoAssistantApp -> CodexAgentLoop.run()`

### 3.3 CodexAgentLoop 的新定位

`CodexAgentLoop` 不再是整个 Agent 的顶层执行器，而改成：

- 某类复杂 node 的执行 backend
- 单子任务求解器
- 可被 workflow node 调用的“局部执行单元”

也就是说：

- 以前：`CodexAgentLoop` = 主运行时
- 以后：`CodexAgentLoop` = 一种 node executor

---

## 4. 核心运行模型

### 4.1 核心对象

建议新增以下核心模型：

#### `WorkflowRun`

表示一次完整用户请求的执行实例。

建议字段：

- `run_id`
- `goal`
- `status`
- `graph`
- `node_states`
- `shared_state`
- `artifacts`
- `references`
- `created_at`
- `updated_at`
- `resume_state`

#### `WorkflowGraph`

表示一个有向图或 DAG。

建议字段：

- `graph_id`
- `nodes: list[WorkflowNode]`
- `edges: list[WorkflowEdge]`
- `entry_node_ids`
- `finish_node_ids`
- `metadata`

#### `WorkflowNode`

表示图中的一个执行单元。

建议字段：

- `node_id`
- `node_type`
- `title`
- `status`
- `depends_on`
- `executor_kind`
- `input_binding`
- `output_key`
- `retry_policy`
- `approval_policy`
- `metadata`

#### `NodeState`

表示节点运行状态。

建议字段：

- `node_id`
- `status`
- `attempt_count`
- `started_at`
- `finished_at`
- `result`
- `error`
- `approval_request`
- `resume_token`

#### `NodeResult`

表示节点输出。

建议字段：

- `summary`
- `structured_output`
- `evidence_items`
- `artifact_ids`
- `references`
- `changed_paths`
- `next_hints`

### 4.2 run 状态机

建议 `WorkflowRun.status` 支持：

- `pending`
- `planning`
- `running`
- `waiting_approval`
- `waiting_input`
- `blocked`
- `failed`
- `completed`
- `cancelled`

### 4.3 node 状态机

建议 `WorkflowNode.status` 支持：

- `pending`
- `ready`
- `running`
- `waiting_approval`
- `waiting_input`
- `completed`
- `failed`
- `skipped`

---

## 5. 节点类型设计

第一版建议支持以下节点类型：

### 5.1 Goal 节点

#### `goal_analysis`
- 识别用户请求的目标、约束、完成标准
- 输出结构化 `GoalSpec`

#### `goal_decomposition`
- 把目标拆成多个子任务
- 输出 `SubTaskSpec[]`

### 5.2 规划节点

#### `graph_build`
- 根据 `SubTaskSpec[]` 构建 workflow graph
- 决定哪些节点串行，哪些可并行

#### `task_plan_build`
- 为某个单子任务生成局部 plan
- 可复用现有 `task_plans.py`

### 5.3 执行节点

#### `workspace_overview`
- 列目录、inspect、representative ranking

#### `file_read`
- resolve target、read/summarize/excerpt

#### `change_apply`
- 代码修改、文本修改、patch 应用

#### `verification`
- run tests / verification command / diff check

#### `search`
- grep、symbol search、resource resolve

#### `codex_subtask`
- 把复杂单子任务委托给 `CodexAgentLoop`
- 作为兼容迁移节点存在

### 5.4 汇总节点

#### `aggregate_results`
- 合并多个节点的 evidence / references / artifacts

#### `final_response`
- 输出最终自然语言回答
- 必须基于聚合结果，不直接使用某个单节点 raw output

### 5.5 控制节点

#### `approval_gate`
- 等待审批
- 恢复后返回下游节点

#### `clarification_gate`
- 等待用户补充信息
- 恢复后重新进入某个节点或局部重建 graph

---

## 6. 执行器设计

### 6.1 执行器接口

建议新增统一节点执行器接口：

```python
class NodeExecutor(Protocol):
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: WorkflowContext) -> NodeResult: ...
```

### 6.2 执行器种类

建议第一版实现：

- `GoalAnalysisExecutor`
- `GoalDecompositionExecutor`
- `GraphBuildExecutor`
- `WorkspaceOverviewExecutor`
- `FileReadExecutor`
- `ChangeApplyExecutor`
- `VerificationExecutor`
- `AggregationExecutor`
- `FinalResponseExecutor`
- `CodexSubtaskExecutor`

### 6.3 `CodexSubtaskExecutor` 的作用

这是重构过渡期最重要的兼容层。

作用：

- 把一个复杂但仍适合“单任务 loop”的子任务，交给 `CodexAgentLoop`
- 让你不必一次性把所有 planner/evaluator/action execution 都重写掉

例如：

- “修改一个文件并运行验证”
- “解释一个模块结构并总结”

在第一版中都可以先作为 `codex_subtask` 节点来跑。

---

## 7. Goal Analysis / Graph Build 设计

### 7.1 GoalAnalysis 输出模型

建议新增：

```python
GoalSpec(
    user_input: str,
    goal_kind: str,
    is_composite: bool,
    success_criteria: list[str],
    constraints: list[str],
    target_hints: list[str],
)
```

### 7.2 GoalDecomposition 输出模型

```python
SubTaskSpec(
    subtask_id: str,
    title: str,
    task_profile: str,
    goal: str,
    target_hint: str,
    depends_on: list[str],
    can_run_parallel: bool,
    expected_output: str,
)
```

### 7.3 GraphBuild 规则

建议编译规则：

- 单目标请求 -> 小图
- 复合请求 -> 多节点图
- 有显式“然后/并且/以及/最后总结” -> 自动插入 `aggregate_results` + `final_response`
- 有修改任务 -> 自动插 `verification`
- 有高风险修改 -> 自动插 `approval_gate`

### 7.4 示例图

请求：

- “帮我列一下当前文件夹都有什么，以及读取一下 README 文件并总结告诉我在讲什么”

建议生成：

1. `goal_analysis`
2. `goal_decomposition`
3. `workspace_overview`
4. `file_read(README.md)`
5. `aggregate_results`
6. `final_response`

其中：

- 3 和 4 可以并行
- 5 依赖 3 和 4
- 6 依赖 5

---

## 8. 调度器设计

### 8.1 Scheduler 职责

调度器应负责：

- 找到 `ready` 节点
- 按依赖关系调度执行
- 控制是否允许并行
- 处理中断 / 审批 / clarification
- 维护 run 状态
- 决定是否继续 / 失败 / 完成

### 8.2 第一版并行策略

建议第一版只支持：

- 无共享写操作节点之间并行
- 任一写 workspace 节点默认串行
- `aggregate_results` / `final_response` 必须串行

### 8.3 重试策略

建议节点级重试，不做全局 run 重试。

规则：

- `search/read/inspect` 可安全重试
- `verification` 可重试
- `write/patch/delete/move` 默认不自动重试
- `approval_gate` / `clarification_gate` 只支持恢复，不支持重试

---

## 9. 共享状态、证据与记忆

### 9.1 Shared State

建议新增 run 级共享状态：

- `resolved_targets`
- `focused_paths`
- `evidence_items`
- `references`
- `artifacts`
- `pending_questions`
- `completed_subgoals`

### 9.2 证据聚合

每个节点输出证据，统一进入 run 级 evidence 池。

建议 evidence 至少记录：

- 来源节点
- 来源工具
- path
- summary
- confidence
- retrievable_for_resolution

### 9.3 记忆写入策略

记忆不应由每个节点随意写入，而应采用：

- 节点输出 -> run evidence pool
- run completion 或关键 checkpoint 时 -> 统一 memory policy 决策

这样可以避免：

- 某个中间节点的低质量输出污染长期记忆

---

## 10. 审批与恢复设计

### 10.1 审批粒度

审批应绑定到节点，而不是绑定到整个 run。

建议：

- node 进入 `waiting_approval`
- run 进入 `waiting_approval`
- 审批通过后只恢复该 node
- 下游节点继续执行

### 10.2 Clarification 恢复

同理：

- 某个 `clarification_gate` 等待用户补充
- 用户回复后把输入绑定回该 gate 节点
- 继续局部图，而不是整轮重开

### 10.3 持久化字段

建议至少持久化：

- `WorkflowRun`
- `WorkflowGraph`
- `NodeState`
- `pending approval`
- `pending clarification`
- `shared_state`

---

## 11. 与现有代码的映射关系

### 11.1 保留模块

建议保留：

- `agent_runtime_framework/tools/*`
- `agent_runtime_framework/resources/*`
- `agent_runtime_framework/memory/*`
- `agent_runtime_framework/models/*`
- `agent_runtime_framework/policy/*`
- `agent_runtime_framework/sandbox/*`
- `agent_runtime_framework/agents/codex/tools.py`
- `agent_runtime_framework/agents/codex/tool_constraints.py`
- `agent_runtime_framework/agents/codex/answer_synthesizer.py`

### 11.2 收缩模块

建议收缩职责：

- `agent_runtime_framework/agents/codex/loop.py`
  - 从主运行时降级为子任务执行器
- `agent_runtime_framework/agents/codex/task_plans.py`
  - 只负责单子任务 plan
- `agent_runtime_framework/agents/codex/evaluator.py`
  - 只做单子任务 evaluator
- `agent_runtime_framework/demo/app.py`
  - 不再直接维护任务级编排逻辑，改为调用 workflow runtime

### 11.3 新增模块

建议新增：

- `agent_runtime_framework/workflow/models.py`
- `agent_runtime_framework/workflow/runtime.py`
- `agent_runtime_framework/workflow/scheduler.py`
- `agent_runtime_framework/workflow/graph_builder.py`
- `agent_runtime_framework/workflow/node_executors.py`
- `agent_runtime_framework/workflow/aggregator.py`
- `agent_runtime_framework/workflow/persistence.py`
- `agent_runtime_framework/workflow/approval.py`
- `agent_runtime_framework/workflow/goal_analysis.py`
- `agent_runtime_framework/workflow/decomposition.py`

---

## 12. 建议的新目录结构

```text
agent_runtime_framework/
  workflow/
    __init__.py
    models.py
    runtime.py
    scheduler.py
    graph_builder.py
    persistence.py
    aggregator.py
    approval.py
    goal_analysis.py
    decomposition.py
    node_executors.py
  agents/
    codex/
      loop.py
      task_plans.py
      evaluator.py
      tools.py
      answer_synthesizer.py
  demo/
    app.py
    server.py
```

---

## 13. 分阶段实施方案

### Phase 1：引入 Workflow 核心骨架

目标：

- 新增 `workflow/` 模块
- 引入 `WorkflowRun / WorkflowGraph / WorkflowNode / NodeState`
- 实现最小 scheduler
- 先不并行

完成标志：

- 单请求可通过 `WorkflowRuntime.run()` 跑完整小图

### Phase 2：接入 GoalAnalysis / Decomposition / GraphBuild

目标：

- 让复合请求能拆出多个子任务
- 让 graph builder 自动插入 `aggregate_results` / `final_response`

完成标志：

- “列目录 + 读 README + 总结” 被稳定拆成多个节点

### Phase 3：接入 CodexSubtaskExecutor

目标：

- 把现有 `CodexAgentLoop` 作为复杂节点执行 backend
- 保障迁移期功能不回退

完成标志：

- 原有单任务测试大部分仍可通过

### Phase 4：接入 node-level approval / resume / clarification

目标：

- 等待审批/补充时恢复到节点级
- run state 可持久化

完成标志：

- 修改/删除类节点具备节点级恢复能力

### Phase 5：引入并行节点调度

目标：

- 只读节点支持有限并行
- 引入 run 级 aggregation

完成标志：

- 复合读取类任务可并行跑两个子节点

### Phase 6：逐步把复杂节点从 CodexSubtaskExecutor 下沉成原生 workflow node

目标：

- 把最常见场景改成原生 node
- 减少对 `CodexAgentLoop` 的依赖

完成标志：

- `CodexAgentLoop` 只保留为兼容执行器，而不是核心运行时

---

## 14. 测试策略

### 14.1 模型层测试

新增单元测试覆盖：

- `WorkflowRun` 状态流转
- `WorkflowGraph` 依赖关系
- `NodeState` 恢复

### 14.2 编译层测试

新增测试覆盖：

- 单任务请求编译成小图
- 复合请求编译成多节点图
- 修改请求自动插 `verification`
- 高风险节点自动插 `approval_gate`

### 14.3 调度层测试

新增测试覆盖：

- ready node 选择
- 串行执行
- 并行读节点执行
- 节点失败停止策略
- 节点审批恢复

### 14.4 端到端测试

新增关键回归：

1. 列目录 + 读 README + 总结
2. 改一个文件 + 跑验证 + 总结
3. 改两个文件 + 跑一次统一验证 + 汇总
4. 歧义目标 -> clarification -> 恢复
5. 删除文件 -> 审批 -> 恢复

---

## 15. 风险与控制

### 风险 1：重写面过大导致短期不可用

控制：

- 通过 `CodexSubtaskExecutor` 复用旧能力
- 新旧运行时并存一段时间

### 风险 2：状态模型变复杂

控制：

- 第一版只做最少节点类型
- 第一版只支持有限并行

### 风险 3：记忆 / 引用 / artifact 汇总混乱

控制：

- 统一走 `run shared_state` 和 `aggregator`
- 禁止节点直接写最终回答

### 风险 4：demo/app 继续过厚

控制：

- 强制 `demo/app.py` 只保留入口编排
- 业务逻辑全部移入 `workflow/`

---

## 16. 最终建议

如果接受大重写，本项目最合理的主方向不是继续补 `CodexAgentLoop`，而是：

- 直接引入 `workflow/` 作为新的唯一主运行时
- 把 `CodexAgentLoop` 改造成兼容型子任务执行器
- 逐步把高频能力迁移成原生 workflow node

换句话说，后续项目的主心骨应变成：

`Workflow Runtime > Node Executors > Tool Runtime > Infra`

而不是：

`Codex Loop > everything`

这是从“单任务工作型 Agent”走向“真正通用工作流 Agent”的关键一步。
