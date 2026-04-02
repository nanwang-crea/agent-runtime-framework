# Agent Graph Runtime Full Migration Implementation Plan

> Status (2026-04-02): 本计划主体已完成并通过当前全量测试。当前代码库保留少量兼容层：`build_workflow_graph()` 作为兼容入口，legacy 节点名仅用于拒绝旧图或术语映射，不再参与主执行路径。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将当前基于 deterministic / model-built workflow graph 的执行系统，完整迁移为“固定骨架 + 动态子图追加 + judge 回环 + persistence/replay/UI 可解释”的 Agent Graph Runtime，并最终完成旧路径与旧语义收口。

**Architecture:** 保留图作为一等执行对象，不退回纯 loop。系统级固定节点负责入口、上下文装配、规划、判定和收敛；中间执行层由 planner 按白名单逐轮生成局部子图并 append 到主图；judge 基于证据、目标覆盖、验证覆盖、歧义和成本控制决定结束、澄清或继续追加子图。

**Tech Stack:** Python 3.12、现有 `WorkflowRuntime` / `WorkflowGraph`、TypedDict/dataclass schema、pytest、demo app runtime、现有 evidence executors。

---

## 0. 设计校正与最终范围

这份计划基于 `docs/plans/2026-04-01-agent-graph-runtime-design.md`，并补全了原设计里尚未展开的“完全收口”部分。最终目标不是“先做一期骨架”，而是完成以下全部状态：

- `build_workflow_graph()` 不再是系统主入口；它最多只保留兼容包装。
- runtime 以 `AgentGraphRuntime` / `AgentGraphState` 为主执行模型。
- planner 只负责一轮局部 `PlannedSubgraph`，不再一次性承诺最终终图。
- judge 成为强制系统控制节点，`final_response` 不得绕过 judge。
- persistence / replay / demo payload / trace / docs 都围绕 agent graph 语义收敛。
- 旧 deterministic graph builder、旧测试语义、旧术语、旧兼容分支在最终阶段完成移除或降级为兼容壳。

---

## 1. 最终交付标准（Definition of Done）

全部完成后，系统应满足：

1. 用户请求进入后，系统首先构造 `GoalEnvelope`，并显式注入 memory / session / workspace / policy。
2. 每一轮 planner 最多追加 3 个（该部分个数应该可以根据配置或者参数进行调整）白名单动态节点，形成 `PlannedSubgraph`。
3. 所有回答必须经过 `judge`，没有 judge 通过就不能进入 `final_response`。
4. 当证据不足、验证不足或目标有歧义时，系统能追加局部子图而不是重建整图。
5. clarification 是图上的受控分支，不是异常退出。
6. persistence 保存：
   - `goal_envelope`
   - `planned_subgraphs`
   - `judge_history`
   - `aggregated_payload`
   - `memory_snapshot`
   - `session_memory_snapshot`
7. replay 能重放到“图增长历史”和“judge 决策历史”。
8. demo / UI payload 能展示：
   - 当前主图或最新子图
   - candidate files
   - chunks
   - evidence items
   - verification by type
   - judge decision
   - 为什么继续 / 为什么停止
9. 现有旧术语与旧兼容路径完成收口，保留范围明确、命名统一。
10. 全量测试通过。

---

## 2. 总体实施阶段

### 阶段 A：建立 Agent Graph 基础模型
### 阶段 B：引入固定骨架节点与上下文装配
### 阶段 C：把 planner 改成局部子图生成器
### 阶段 D：支持主图 append 与多轮执行
### 阶段 E：引入 judge 驱动收敛 / 回环 / 澄清
### 阶段 F：改造 final_response、verification、受限收敛
### 阶段 G：迁移 demo、stream/chat、payload、trace
### 阶段 H：迁移 persistence / replay / observability
### 阶段 I：清理旧入口、旧 graph builder 语义、旧术语与文档
### 阶段 J：全量回归与发布前收口

每个阶段都必须完成：
- failing tests
- 最小实现
- 阶段内定向测试
- 必要文档更新

---

## 3. Task Breakdown

### Task 1: 定义 Agent Graph 核心模型

**Files:**
- Modify: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_workflow_models.py`

**目标：** 新增并稳定以下模型：
- `GoalEnvelope`
- `PlannedNode`
- `PlannedSubgraph`
- `JudgeDecision`
- `AgentGraphState`
- 必要的 normalize / serialize helper

**Step 1: Write the failing test**
- 新增测试覆盖：
  - dataclass 构造
  - 默认值
  - 可序列化 payload helper
  - 从空状态构造 `AgentGraphState`

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_models.py -q`
Expected: FAIL，提示缺失模型或 helper

**Step 3: Write minimal implementation**
- 在 `models.py` 中补充新 dataclass / TypedDict
- 保持现有 `WorkflowGraph` / `WorkflowRun` 不破

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_models.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/models.py tests/test_workflow_models.py
git commit -m "feat: add agent graph core models"
```

---

### Task 2: 新增 goal_intake 与 context_assembly

**Files:**
- Create: `agent_runtime_framework/workflow/goal_intake.py`
- Create: `agent_runtime_framework/workflow/context_assembly.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_demo_app.py`
- Test: `tests/test_memory_and_policy.py`

**目标：** 统一构造 `GoalEnvelope`，把 memory / session / workspace / policy 明确作为入口上下文，而不是散落在 runtime 临时 dict 中。

**Step 1: Write the failing test**
- `goal_intake` 输出包含：
  - `goal`
  - `intent`
  - `target_hints`
  - `memory_snapshot`
  - `workspace_snapshot`
  - `constraints`
- `context_assembly` 输出包含：
  - `application_context`
  - `workspace_context`
  - `memory`
  - `session_memory_snapshot`
  - `policy_context`

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_demo_app.py tests/test_memory_and_policy.py -q`
Expected: FAIL，提示 helper 或 payload 不存在

**Step 3: Write minimal implementation**
- `goal_intake.py` 封装现有 `analyze_goal()` 与 memory snapshot 收集
- `context_assembly.py` 统一输出 runtime context
- `DemoAssistantApp` 调用新入口 helper

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_demo_app.py tests/test_memory_and_policy.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/goal_intake.py agent_runtime_framework/workflow/context_assembly.py agent_runtime_framework/demo/app.py tests/test_demo_app.py tests/test_memory_and_policy.py
git commit -m "feat: add goal intake and context assembly"
```

---

### Task 3: 新增 planner_v2，专门生成局部子图

**Files:**
- Create: `agent_runtime_framework/workflow/planner_v2.py`
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Test: `tests/test_workflow_graph_builder.py`

**目标：** 把当前 `build_workflow_graph()` 的职责拆分：
- 旧入口：兼容包装
- 新入口：`plan_next_subgraph(goal_envelope, graph_state, context)`

**Step 1: Write the failing test**
- planner_v2 只能从白名单输出节点
- 每轮最多 3 个动态节点
- 输出必须带 `reason` 和 `success_criteria`
- 不允许出现 `final_response`

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_graph_builder.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 引入 `planner_v2.py`
- 先用 deterministic fallback 实现一版“局部子图 planner”
- `graph_builder.py` 增加兼容层：旧接口内部可委托给 planner_v2 生成首轮骨架或兼容图

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_graph_builder.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/planner_v2.py agent_runtime_framework/workflow/graph_builder.py tests/test_workflow_graph_builder.py
git commit -m "feat: add subgraph planner v2"
```

---

### Task 4: 新增 graph_mutation，支持局部子图 append

**Files:**
- Create: `agent_runtime_framework/workflow/graph_mutation.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_workflow_runtime.py`

**目标：** 允许在已有 `WorkflowGraph` 上 append 局部子图，并记录追加来源。

**Step 1: Write the failing test**
- append 后节点顺序正确
- edges 正确接到上一个 `judge` 或 planner anchor
- 重复 node_id 会被拒绝
- appended metadata 包含 iteration / parent_judge_id

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_runtime.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 实现 `append_subgraph(graph, subgraph, after_node_id)`
- graph metadata 中写入 append history

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_runtime.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/graph_mutation.py agent_runtime_framework/workflow/models.py tests/test_workflow_runtime.py
git commit -m "feat: support planned subgraph append"
```

---

### Task 5: 新增 AgentGraphRuntime 与 AgentGraphState 驱动执行

**Files:**
- Create: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Modify: `agent_runtime_framework/workflow/runtime.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Test: `tests/test_workflow_runtime.py`

**目标：** 在不破坏现有 `WorkflowRuntime` 的前提下，引入一个更高层的运行器：
- 维护 `AgentGraphState`
- 协调 `goal_intake -> plan -> append -> execute -> judge`

**Step 1: Write the failing test**
- 1 轮 accepted 能完成
- 1 轮不够会进入第 2 轮 append
- iteration 超限会走 limited answer

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_runtime.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- `AgentGraphRuntime.run()`
- 先只支持串行 batch 执行
- 每轮执行后更新 `AgentGraphState`

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_runtime.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/agent_graph_runtime.py agent_runtime_framework/workflow/runtime.py agent_runtime_framework/workflow/__init__.py tests/test_workflow_runtime.py
git commit -m "feat: add agent graph runtime"
```

---

### Task 6: 新增 judge，并把它变成强制系统控制节点

**Files:**
- Create: `agent_runtime_framework/workflow/judge.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_workflow_runtime.py`

**目标：** 让 judge 统一处理：
- evidence sufficiency
- goal coverage
- verification coverage
- ambiguity
- cost control

**Step 1: Write the failing test**
- `accepted`
- `needs_more_evidence`
- `needs_verification`
- `needs_clarification`
- `stop_due_to_cost`

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_runtime.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 先规则优先：
  - ambiguity / iteration / verification 缺失用规则判断
- 再加模型辅助：
  - goal coverage / evidence sufficiency

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_runtime.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/judge.py agent_runtime_framework/workflow/node_executors.py agent_runtime_framework/workflow/models.py tests/test_workflow_runtime.py
git commit -m "feat: add judge-controlled graph decisions"
```

---

### Task 7: 固定 final_response 的进入条件

**Files:**
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Test: `tests/test_workflow_runtime.py`

**目标：** `final_response` 只能在：
- `judge.status == accepted`
- 或 `judge.status == stop_due_to_cost`
时执行。

**Step 1: Write the failing test**
- judge 未通过时尝试进入 `final_response` 应被拦截
- limited answer 应包含缺失项和停止原因

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_runtime.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- runtime 根据 judge decision 决定是否注入 `final_response`
- `FinalResponseExecutor` 接受 judge output 作为输入之一

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_runtime.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/node_executors.py agent_runtime_framework/workflow/agent_graph_runtime.py tests/test_workflow_runtime.py
git commit -m "feat: gate final response behind judge"
```

---

### Task 8: 改造 clarification 为正式图分支

**Files:**
- Modify: `agent_runtime_framework/workflow/clarification_executor.py`
- Modify: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_demo_app.py`
- Test: `tests/test_workflow_runtime.py`

**目标：** clarification 不再只是共享态中的 side effect，而是：
- 图上的受控分支节点
- 可 replay
- 可 persistence
- 可重新进入 planner

**Step 1: Write the failing test**
- 多目标歧义请求进入 clarification 分支
- 用户回复后从 `plan_(n+1)` 继续，而不是重建首轮图

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_demo_app.py tests/test_workflow_runtime.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- `JudgeDecision(status="needs_clarification")`
- runtime 将 clarification 作为显式下一步
- clarification 完成后把用户回答注入下一轮 planner 输入

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_demo_app.py tests/test_workflow_runtime.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/clarification_executor.py agent_runtime_framework/workflow/agent_graph_runtime.py agent_runtime_framework/demo/app.py tests/test_demo_app.py tests/test_workflow_runtime.py
git commit -m "feat: promote clarification to graph branch"
```

---

### Task 9: 统一 verification_step 与 verification_by_type

**Files:**
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/workflow/judge.py`
- Test: `tests/test_workflow_runtime.py`

**目标：** 把现有 verification 结果统一接入 judge：
- `evidence`
- `tool`
- `test`
- `approval`

**Step 1: Write the failing test**
- judge 判断 `needs_verification`
- verification 补齐后转为 `accepted`

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_runtime.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- `verification_by_type` 接入 judge 的 `coverage_report`
- `verification_step` 可被 planner 显式追加

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_runtime.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/node_executors.py agent_runtime_framework/workflow/judge.py tests/test_workflow_runtime.py
git commit -m "feat: integrate typed verification into judge"
```

---

### Task 10: demo/app 接入 Agent Graph Runtime

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_demo_app.py`

**目标：** DemoAssistantApp 不再直接依赖“整图一次执行”的模型，而是：
- 用 `goal_intake`
- 用 `AgentGraphRuntime`
- 对外保持 `chat()` / `stream_chat()` 接口不变

**Step 1: Write the failing test**
- chat 与 stream 共用同一 compile/plan/execute 语义
- trace 中出现 planner / judge / clarification / append history
- evidence payload 包含 judge decision 与 current subgraph

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_demo_app.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- `DemoAssistantApp` 改用 `AgentGraphRuntime`
- payload 增加：
  - `judge`
  - `planned_subgraphs`
  - `graph_state_summary`

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_demo_app.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/demo/app.py tests/test_demo_app.py
git commit -m "feat: wire demo app to agent graph runtime"
```

---

### Task 11: persistence / replay 存储 AgentGraphState

**Files:**
- Modify: `agent_runtime_framework/workflow/persistence.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_workflow_persistence.py`
- Test: `tests/test_demo_app.py`

**目标：** 持久化以下对象：
- `goal_envelope`
- `planned_subgraphs`
- `judge_history`
- `aggregated_payload`
- `memory_snapshot`
- `session_memory_snapshot`
- graph append history

**Step 1: Write the failing test**
- save/load 后：
  - 能恢复多轮 appended graph
  - 能恢复 judge history
  - replay 能看到相同 evidence/judge 结构

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_persistence.py tests/test_demo_app.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- persistence 增加 `_restore_agent_graph_state()`
- `shared_state` 与 `graph.metadata` 中保留 append history

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_persistence.py tests/test_demo_app.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/persistence.py agent_runtime_framework/demo/app.py tests/test_workflow_persistence.py tests/test_demo_app.py
git commit -m "feat: persist agent graph state and replay metadata"
```

---

### Task 12: observability 与 UI payload 对齐

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_demo_app.py`

**目标：** 前端/trace 输出应能直接显示：
- 当前 iteration
- 当前 planner 产出的子图
- judge 决策
- 当前 evidence
- 为什么继续 / 为什么停

**Step 1: Write the failing test**
- payload 中包含：
  - `judge`
  - `planned_subgraphs`
  - `graph_state_summary`
  - `append_history`

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_demo_app.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 扩展 demo payload
- trace step detail 支持 planner/judge 描述

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_demo_app.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/demo/app.py agent_runtime_framework/workflow/models.py tests/test_demo_app.py
git commit -m "feat: expose agent graph planning and judge traces"
```

---

### Task 13: 将现有 deterministic graph builder 降级为兼容层

**Files:**
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Test: `tests/test_workflow_graph_builder.py`

**目标：**
- 旧 `build_workflow_graph()` 只保留兼容作用
- 新主入口明确为：
  - `build_goal_envelope`
  - `plan_next_subgraph`
  - `append_subgraph`
  - `AgentGraphRuntime`

**Step 1: Write the failing test**
- 旧接口仍可兼容调用
- 但内部 metadata 应明确标记 `compatibility_mode`
- 新测试主用新接口

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_graph_builder.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- graph_builder 增加 deprecation/compat layer
- 新接口在 `__init__.py` 导出

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_graph_builder.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/graph_builder.py agent_runtime_framework/workflow/__init__.py tests/test_workflow_graph_builder.py
git commit -m "refactor: reduce graph builder to compatibility layer"
```

---

### Task 14: 收口旧术语与旧测试语义

**Files:**
- Modify: `tests/test_workflow_decomposition.py`
- Modify: `tests/test_workflow_codex_subtask.py`
- Modify: `agent_runtime_framework/agents/builtin.py`
- Modify: `agent_runtime_framework/agents/workspace_backend/personas.py`
- Modify: `docs/通用Agent.md`
- Modify: `docs/plans/2026-04-01-workflow-terminology-glossary.md`

**目标：**
- 将残留 `repository_explainer` / `file_reader` 语义统一替换或明确标注为兼容层。
- decomposition 测试与 doc 全部转为 agent graph 术语。

**Step 1: Write the failing test**
- 搜索断言：关键路径不再依赖旧术语
- decomposition / codex_subtask 使用新术语

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_decomposition.py tests/test_workflow_codex_subtask.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 统一测试与文档术语
- 必要的兼容映射写明边界

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_decomposition.py tests/test_workflow_codex_subtask.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add tests/test_workflow_decomposition.py tests/test_workflow_codex_subtask.py agent_runtime_framework/agents/builtin.py agent_runtime_framework/agents/workspace_backend/personas.py docs/通用Agent.md docs/plans/2026-04-01-workflow-terminology-glossary.md
git commit -m "chore: converge terminology on agent graph runtime"
```

---

### Task 15: 删除或冻结旧路径

**Files:**
- Modify: `agent_runtime_framework/workflow/response_synthesis_executor.py`
- Modify: `agent_runtime_framework/workflow/file_inspection_executor.py`
- Modify: `agent_runtime_framework/workflow/target_resolution_executor.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_demo_app.py`

**目标：** 明确旧路径处理策略：
- 保留 `target_resolution` 作为白名单动态节点
- `file_inspection` / `response_synthesis` 彻底移除或转成 deprecated shim
- demo runtime 不再注册任何不属于 Agent Graph Runtime 的旧节点

**Step 1: Write the failing test**
- 旧节点若仍存在，只允许作为 deprecated shim 被拒绝或重定向
- 主路径只能经过新 agent graph 语义

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_workflow_runtime.py tests/test_demo_app.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 删除或冻结旧 executor
- 调整 runtime registry

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_workflow_runtime.py tests/test_demo_app.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add agent_runtime_framework/workflow/response_synthesis_executor.py agent_runtime_framework/workflow/file_inspection_executor.py agent_runtime_framework/workflow/target_resolution_executor.py agent_runtime_framework/demo/app.py tests/test_workflow_runtime.py tests/test_demo_app.py
git commit -m "refactor: remove obsolete non-agent-graph execution paths"
```

---

### Task 16: 全量回归、文档收尾、发布前检查

**Files:**
- Modify: `docs/plans/2026-04-01-agent-graph-runtime-design.md`
- Modify: `docs/plans/2026-04-01-agent-graph-runtime-full-implementation-plan.md`
- Test: all relevant tests

**目标：**
- 核对设计与实现一致
- 更新最终文档
- 完成全量测试与收尾

**Step 1: Run focused suites**
Run:
```bash
pytest tests/test_workflow_models.py tests/test_workflow_runtime.py tests/test_workflow_graph_builder.py tests/test_workflow_persistence.py tests/test_demo_app.py -q
```
Expected: PASS

**Step 2: Run full test suite**
Run:
```bash
pytest -q
```
Expected: PASS

**Step 3: Review docs for final terminology**
- 更新 design 文档中的“计划中”描述为实际状态
- 补一段迁移摘要与系统入口说明

**Step 4: Commit**
```bash
git add docs/plans/2026-04-01-agent-graph-runtime-design.md docs/plans/2026-04-01-agent-graph-runtime-full-implementation-plan.md
 git commit -m "docs: finalize agent graph runtime migration plan"
```

---

## 4. 测试矩阵（完整覆盖）

### 单元层
- `tests/test_workflow_models.py`
- `tests/test_workflow_runtime.py`
- `tests/test_workflow_graph_builder.py`
- `tests/test_workflow_persistence.py`

### demo / app 层
- `tests/test_demo_app.py`

### memory / policy / planner 层
- `tests/test_memory_and_policy.py`
- `tests/test_workflow_decomposition.py`
- `tests/test_workflow_codex_subtask.py`

### 最终全量
Run: `pytest -q`
Expected: PASS

---

## 5. 风险与应对

### 风险 1：主图 append 后状态难以推理
**应对：** 每轮 append history 入 graph metadata；judge 与 planner 输出必须带 iteration。

### 风险 2：planner 输出质量不稳定
**应对：** 先 deterministic fallback，再引入 model planner；白名单与 step budget 强约束。

### 风险 3：旧测试大量依赖 `build_workflow_graph`
**应对：** 先保留兼容层，等新测试体系稳定后再收缩旧入口。

### 风险 4：clarification 与 replay 交织复杂
**应对：** clarification 明确建模为图分支节点，不继续放在隐式 shared_state side effect。

### 风险 5：阶段中途“半 agent graph、半 workflow”长期共存
**应对：** Task 13-15 明确要求完成入口与旧路径收口，而不是无限兼容。

---

## 6. 最终建议的执行顺序

严格按以下顺序执行，不跳步：
1. 核心模型
2. goal/context 装配
3. planner_v2
4. graph append
5. AgentGraphRuntime
6. judge
7. final_response gating
8. clarification 分支
9. verification 融合
10. demo 接入
11. persistence/replay
12. observability/UI payload
13. 降级旧 graph_builder
14. 术语与文档收口
15. 删除/冻结旧路径
16. 全量回归

---

## 7. 完成后的系统心智模型

完成后，这个仓库不再是：
- 一次性生成整图并跑到底的 workflow engine

而是：
- **以图为主执行对象的 Agent Runtime**
- 固定系统骨架节点
- 中间按 judge 结果动态追加局部子图
- evidence / verification / clarification / replay / UI 全部围绕这个 Agent Graph 语义收敛

一句话总结：
- **图还在，而且更强了；它从静态 workflow graph 升级成了可增长、可判定、可解释的 Agent Graph。**
