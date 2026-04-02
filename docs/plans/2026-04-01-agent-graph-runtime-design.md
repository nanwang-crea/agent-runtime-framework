# Agent Graph Runtime Implementation Plan

> Status (2026-04-02): 核心设计已落地。`AgentGraphRuntime`、`GoalEnvelope`、`planner_v2`、`judge`、append history、persistence/replay、demo payload 均已接入；`build_workflow_graph()` 现仅作为兼容入口并带 `compatibility_mode` 标记。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将当前 evidence workflow 升级为“固定骨架 + 动态中段 + judge 回环”的 Agent Graph Runtime，同时保留图结构作为一等执行对象。

**Architecture:** 系统保留图执行模型，但把一次性整图生成改成“主图固定、运行时追加子图”。入口层与收敛层固定，中间执行层由 planner 按白名单动态生成局部子图；judge 根据证据充分性、目标覆盖率、验证覆盖率与歧义状态决定结束、澄清或回到 plan 追加下一段子图。

**Tech Stack:** Python 3.12、现有 `WorkflowRuntime` / `WorkflowGraph`、TypedDict/dataclass schema、pytest、demo app runtime。

---

## 当前职责边界（2026-04-02 更新）

- `DemoAssistantApp`：负责 session、外部交互入口、payload 汇总与 composition root，不再承担 root-level goal analysis 的隐藏副作用。
- `DemoRuntimeFactory`：负责 wiring 与具名服务装配，不再使用关键业务 lambda 作为长期架构边界。
- `RootGraphRuntime`：负责 root route orchestration、route trace、conversation/agent 分流。
- `AgentGraphRuntime`：负责 planner/judge 回环、子图 append 与 agent graph 状态推进。
- `CompatWorkflowRunner`：属于兼容层，用于 legacy workflow graph 执行路径；不是长期唯一执行入口。
- `WorkflowRuntime`：负责节点级图执行，是 runtime kernel，不直接感知 demo app 细节。

## 兼容层说明（2026-04-02 更新）

- `compile_compat_workflow_graph(...)` 与 `CompatWorkflowRunner` 仍然保留，用于 legacy/compatibility mode。
- 新的长期方向是：root route -> agent/conversation branch -> typed runtime context -> standardized diagnostics。
- model-backed workflow 路径现在统一支持 object/dict 两种 `context` 取法，但后续仍建议继续收敛到单一协议。

---

## 1. 目标与非目标

### 目标
- 保留基于图的执行框架，而不是退回纯 while-loop orchestrator。
- 固定首尾系统节点：`goal_intake`、`context_assembly`、`plan`、`judge`、`final_response`。
- 中间执行节点由模型按白名单动态规划，形成 `PlannedSubgraph` 并 append 到当前 run graph。
- `judge` 成为系统级控制节点：判断继续补证据、补验证、请求澄清或结束。
- memory / session / workspace / policy 在入口统一注入，并贯穿后续所有 planner/executor/judge。
- persistence / replay / UI 都能解释“为什么追加这一段子图”“为什么当前停在这里”。

### 非目标
- 不在第一阶段支持任意复杂图重写或图裁剪。
- 不在第一阶段引入并发子图调度器。
- 不在第一阶段把所有 executor 改写成全新接口；优先包装和复用现有 executor。
- 不要求一次完成前端完整可视化编辑器。

---

## 2. 架构草图

### 2.1 顶层图结构

```text
goal_intake
  -> context_assembly
  -> plan_1
  -> [dynamic_subgraph_1]
  -> aggregate_results_1
  -> evidence_synthesis_1
  -> judge_1
      -> final_response
      -> clarification
      -> plan_2
  -> [dynamic_subgraph_2]
  -> aggregate_results_2
  -> evidence_synthesis_2
  -> judge_2
      -> final_response
      -> plan_3
```

### 2.2 关键思想
- 主图是长期对象：一次 run 内持续存在。
- planner 每轮只生成一小段局部子图，而不是最终全图。
- judge 不通过时，系统把下一轮 planner 产出的子图 append 到当前图上。
- `final_response` 只能由 `judge.status == accepted` 进入。
- clarification 不再被视为异常，而是图上的受控分支。

### 2.3 系统级节点与业务节点

#### 系统级节点（固定）
- `goal_intake`
- `context_assembly`
- `plan`
- `judge`
- `final_response`

#### 动态业务节点（白名单）
- `target_resolution`
- `workspace_discovery`
- `content_search`
- `chunked_file_read`
- `workspace_subtask`
- `tool_call`
- `verification_step`
- `aggregate_results`
- `evidence_synthesis`

---

## 3. 数据模型设计

### 3.1 `GoalEnvelope`
建议放到 `agent_runtime_framework/workflow/models.py`。

```python
@dataclass(slots=True)
class GoalEnvelope:
    goal: str
    normalized_goal: str
    intent: str
    target_hints: list[str]
    memory_snapshot: dict[str, Any]
    workspace_snapshot: dict[str, Any]
    policy_context: dict[str, Any]
    constraints: dict[str, Any]
    success_criteria: list[str]
```

### 3.2 `PlannedNode`

```python
@dataclass(slots=True)
class PlannedNode:
    node_id: str
    node_type: str
    reason: str
    inputs: dict[str, Any]
    depends_on: list[str]
    success_criteria: list[str]
```

### 3.3 `PlannedSubgraph`

```python
@dataclass(slots=True)
class PlannedSubgraph:
    iteration: int
    planner_summary: str
    nodes: list[PlannedNode]
    edges: list[WorkflowEdge]
```

### 3.4 `JudgeDecision`

```python
@dataclass(slots=True)
class JudgeDecision:
    status: Literal[
        "accepted",
        "needs_more_evidence",
        "needs_verification",
        "needs_clarification",
        "stop_due_to_cost",
    ]
    reason: str
    missing_evidence: list[str]
    coverage_report: dict[str, Any]
    replan_hint: dict[str, Any]
```

### 3.5 `AgentGraphState`

```python
@dataclass(slots=True)
class AgentGraphState:
    run_id: str
    goal_envelope: GoalEnvelope
    current_iteration: int
    aggregated_payload: AggregatedWorkflowPayload
    planned_subgraphs: list[PlannedSubgraph]
    judge_history: list[JudgeDecision]
    appended_node_ids: list[str]
```

---

## 4. 模块拆分

### 新增模块
- `agent_runtime_framework/workflow/goal_intake.py`
  - `build_goal_envelope(message, context) -> GoalEnvelope`
- `agent_runtime_framework/workflow/context_assembly.py`
  - `build_runtime_context(goal_envelope, app_context, workspace_context) -> dict[str, Any]`
- `agent_runtime_framework/workflow/planner_v2.py`
  - `plan_next_subgraph(goal_envelope, graph_state, context) -> PlannedSubgraph`
- `agent_runtime_framework/workflow/judge.py`
  - `judge_progress(goal_envelope, aggregated_payload, graph_state) -> JudgeDecision`
- `agent_runtime_framework/workflow/graph_mutation.py`
  - `append_subgraph(graph, subgraph, *, after_node_id) -> WorkflowGraph`
- `agent_runtime_framework/workflow/agent_graph_runtime.py`
  - `run_agent_graph(goal_envelope, context) -> WorkflowRun`

### 需要调整的现有模块
- `agent_runtime_framework/workflow/models.py`
- `agent_runtime_framework/workflow/graph_builder.py`
- `agent_runtime_framework/workflow/runtime.py`
- `agent_runtime_framework/demo/app.py`
- `agent_runtime_framework/workflow/persistence.py`

---

## 5. 运行语义

### 5.1 首轮
1. `goal_intake` 构造 `GoalEnvelope`
2. `context_assembly` 注入 memory / session / workspace / policy
3. `plan_1` 生成 `PlannedSubgraph(iteration=1)`
4. 将子图 append 到主图
5. 执行该子图
6. 聚合 `AggregatedWorkflowPayload`
7. `judge_1` 给出 `JudgeDecision`

### 5.2 judge 分支
- `accepted` -> `final_response`
- `needs_more_evidence` -> `plan_(n+1)`
- `needs_verification` -> `plan_(n+1)`
- `needs_clarification` -> `clarification`
- `stop_due_to_cost` -> `final_response`（受限答案）

### 5.3 最大迭代控制
- `max_iterations = 3`
- 超限后不能直接失败，应输出：
  - 当前已有证据
  - 缺失证据
  - 停止原因

---

## 6. Planner 约束

Planner 必须满足：
- 只能从注册白名单里选择节点类型。
- 每轮最多规划 3 个动态执行节点。
- 必须为每个节点提供 `reason`。
- 必须为每个节点提供 `success_criteria`。
- 不允许直接生成 `final_response`。
- 不允许绕过 `judge`。
- 推荐优先重用已产生的 evidence，而不是盲目追加新检索。

建议在 `planner_v2.py` 中把系统 prompt 明确成：
- 你只负责“下一轮局部子图”
- 不负责最终答案
- 不负责决定终止
- 终止权归 `judge`

---

## 7. Judge 标准

Judge 至少检查以下五类维度：

### 7.1 evidence sufficiency
- 是否已有足够候选文件 / chunks / facts 支撑回答
- 是否只命中文档却未命中实现源码

### 7.2 goal coverage
- 当前聚合结果是否真的回答了用户问题
- 是否只是罗列材料，没有形成答案

### 7.3 verification coverage
- 用户要求验证时，是否已完成对应验证
- 是否缺失 evidence/tool/test/approval 某类验证

### 7.4 ambiguity
- 是否还存在多个可能目标
- 是否需要请求用户澄清

### 7.5 cost control
- 继续追加一轮的收益是否足够高
- 是否应该受限收敛

Judge 输出示例：
- `accepted`: 已定位唯一目标，且关键实现片段已阅读，证据足够形成回答。
- `needs_more_evidence`: 当前只命中说明文档，缺少实现层证据，建议补读 `src/...`。
- `needs_verification`: 已给出修改建议，但缺少测试/命令验证。
- `needs_clarification`: `service` 同时命中 `src/service.py` 与 `docs/service.md`。
- `stop_due_to_cost`: 已经过 3 轮追加，新增收益低，建议输出受限答案。

---

## 8. 对当前仓库的最小改造路线

### 阶段 1：在现有 runtime 外包一层 Agent Graph 控制
**Files:**
- Create: `agent_runtime_framework/workflow/goal_intake.py`
- Create: `agent_runtime_framework/workflow/context_assembly.py`
- Create: `agent_runtime_framework/workflow/judge.py`
- Create: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_demo_app.py`
- Test: `tests/test_workflow_runtime.py`

**目标：**
- 不动现有 executor 行为
- 先把它们放进固定骨架中运行
- `graph_builder` 仍可暂时产 deterministic 图

### 阶段 2：把 `graph_builder` 降级成子图 planner
**Files:**
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Create: `agent_runtime_framework/workflow/planner_v2.py`
- Test: `tests/test_workflow_graph_builder.py`

**目标：**
- 不再一次性构造最终终图
- 只返回一轮 `PlannedSubgraph`

### 阶段 3：引入图 append 机制
**Files:**
- Create: `agent_runtime_framework/workflow/graph_mutation.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Modify: `agent_runtime_framework/workflow/runtime.py`
- Test: `tests/test_workflow_runtime.py`

**目标：**
- 允许在当前 run graph 上追加节点与边
- 保持 replay 可追溯

### 阶段 4：收敛 final_response 权限
**Files:**
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/workflow/judge.py`
- Test: `tests/test_workflow_runtime.py`

**目标：**
- `final_response` 只接受 judge 已通过的结果

### 阶段 5：持久化与 UI 对齐
**Files:**
- Modify: `agent_runtime_framework/workflow/persistence.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_workflow_persistence.py`
- Test: `tests/test_demo_app.py`

**目标：**
- 保存 `goal_envelope / planned_subgraphs / judge_history / memory snapshots`
- demo payload 直接展示 judge 与追加子图历史

---

## 9. 具体实现建议

### 9.1 `goal_intake.py`
输出内容建议包括：
- 用户原始输入
- 归一化 goal
- 记忆快照
- 最近 focus 资源
- workspace 摘要
- agent/profile/sandbox 约束

### 9.2 `planner_v2.py`
建议 planner 输入：
- `GoalEnvelope`
- 当前 `AggregatedWorkflowPayload`
- 上一轮 `JudgeDecision`
- 当前 iteration

建议输出：
- 1~3 个节点
- 局部边关系
- planner summary

### 9.3 `graph_mutation.py`
核心函数：
- `append_subgraph(graph, subgraph, after_node_id)`
- `wire_subgraph_to_judge(graph, subgraph, judge_anchor)`

### 9.4 `judge.py`
建议先规则优先、模型辅助：
- ambiguity / verification / iteration limit 用规则判断
- goal coverage / evidence sufficiency 可用模型辅助判断

### 9.5 `demo/app.py`
建议 DemoAssistantApp 最终暴露：
- 当前主图
- 本轮追加子图
- judge decision
- aggregated evidence
- final answer / limited answer

---

## 10. 测试策略

### 单元测试
- `tests/test_goal_intake.py`
- `tests/test_judge.py`
- `tests/test_planner_v2.py`
- `tests/test_graph_mutation.py`

### 回归测试
- `stream_chat` 与 `chat` 共享单一路径，不重复 plan
- memory 在 `goal_intake` 后可被 planner/executor 读到
- judge 不通过时会追加子图，而不是整图重建
- clarification 会进入受控分支，而不是异常失败
- final_response 只能在 accepted 时出现

### 集成测试
- 单目标解释：1 轮 accepted
- 多目标歧义：judge -> clarification
- 证据不足：judge -> replan -> accepted
- 需要验证：judge -> verification_step -> accepted
- 超出上限：limit_reached -> limited final_response

---

## 11. 风险与控制

### 风险 1：Planner 失控生成无意义节点
**控制：** 白名单 + 每轮节点数上限 + success criteria 必填

### 风险 2：judge 过度回环导致成本失控
**控制：** `max_iterations` + cost-based stop

### 风险 3：append graph 后 replay 变复杂
**控制：** persistence 保存每轮 `PlannedSubgraph` 与 `JudgeDecision`

### 风险 4：旧 deterministic graph 与新 agent graph 并存混乱
**控制：** 第一阶段保留兼容开关，第二阶段逐步收敛到 planner_v2

---

## 12. 推荐命名

推荐正式命名为：
- **Agent Graph Runtime**
- 或：**Evidence-Driven Agent Graph Runtime**

不建议继续只叫：
- `workflow graph`

因为它已经不再是普通 workflow，而是：
- 有 memory
- 有 judge
- 有 replan
- 有 clarification
- 有受控动态图 append

---

## 13. 第一批落地文件建议

优先顺序：
1. `agent_runtime_framework/workflow/goal_intake.py`
2. `agent_runtime_framework/workflow/judge.py`
3. `agent_runtime_framework/workflow/agent_graph_runtime.py`
4. `agent_runtime_framework/demo/app.py`
5. `agent_runtime_framework/workflow/models.py`
6. `tests/test_demo_app.py`
7. `tests/test_workflow_runtime.py`

原因：
- 先把框架骨架立起来
- 再逐步把 planner / graph mutation / persistence 接进去
- 可以最小化对现有 executor 的扰动

---

## 14. 实施建议总结

最终推荐方向：
- 保留图结构
- 固定系统骨架节点
- 中间子图由 planner 逐轮动态追加
- judge 成为系统级控制节点
- final_response 只在 judge accepted 时收敛
- memory 在入口统一注入

一句话总结：
- **不是放弃 Graph 改成纯 Loop**
- **而是让 Graph 拥有 AgentLoop 的运行语义**
