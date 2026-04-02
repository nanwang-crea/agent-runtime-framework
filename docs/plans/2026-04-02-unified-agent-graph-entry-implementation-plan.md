# Unified Agent Graph Entry Implementation Plan

> Status (2026-04-02): demo 主入口已从 `build_workflow_graph()` 主路径判定中摘除。conversation 请求走 conversation graph，普通 workspace 请求走 `AgentGraphRuntime`。审批也已并入 Agent Graph：高风险节点在执行期动态进入 `waiting_approval`，审批后继续在同一图内恢复执行。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 统一用户输入的顶层执行入口：闲聊走轻量 conversation graph 直接进入 `final_response`，非闲聊统一走 `AgentGraphRuntime` 的 `plan -> dynamic subgraph -> judge -> loop/final_response` 主路径，并把 `build_workflow_graph()` 降为真正的兼容 API。

**Architecture:** 顶层入口不再依赖“先整图编译、再根据图判断路线”的旧模型。入口先做 goal 分类：conversation 请求构造极简 graph（`conversation_response -> final_response`）且不进入 judge；非 conversation 请求直接构造 `GoalEnvelope` 并进入 `AgentGraphRuntime`。`build_workflow_graph()` 只保留给兼容测试、legacy graph 拒绝、必要 fallback 场景使用。

**Tech Stack:** Python 3.12、`AgentGraphRuntime`、`WorkflowRuntime`、`WorkflowGraph`、pytest、demo app runtime。

---

## 0. 统一后的目标心智模型

完成后，系统应只有两条明确主链：

1. **Conversation Graph**
   - `goal_intake`
   - `conversation_response`
   - `final_response`
   - 无 `judge`

2. **Agent Graph**
   - `goal_intake`
   - `context_assembly`
   - `plan_n`
   - `dynamic_subgraph_n`
   - `aggregate/evidence`
   - `judge_n`
   - `accepted -> final_response`
   - `not accepted -> plan_(n+1)`

顶层 demo 入口不再通过 `build_workflow_graph()` 决定主路径。

---

## 1. Definition of Done

全部完成后，系统必须满足：

1. `DemoAssistantApp.chat()` 与 `DemoAssistantApp.stream_chat()` 都不再依赖 `build_workflow_graph()` 作为主路径判定输入。
2. conversation 请求使用统一的 conversation graph 语义，而不是 app 层特殊流式分支绕开 `final_response`。
3. 非 conversation 请求统一进入 `AgentGraphRuntime`，不再通过 deterministic full graph 先判再转。
4. `build_workflow_graph()` 不再被 demo 主路径直接使用，只保留兼容 API 定位。
5. legacy 节点名（如 `file_reader` / `repository_explainer`）仅用于兼容测试或拒绝旧图，不参与主路径路由判断。
6. 全量测试通过。

---

## 2. Task Breakdown

### Task 1: 为统一入口补测试，锁定新行为

**Files:**
- Modify: `tests/test_demo_app.py`
- Modify: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing test**
- 新增/修改测试覆盖：
  - `chat()` 主路径不依赖 `build_workflow_graph()` 判定非闲聊工作流
  - `stream_chat()` 主路径不依赖 `build_workflow_graph()` 判定非闲聊工作流
  - conversation 请求仍能返回 `final_response` 语义 payload
  - `build_workflow_graph()` 在 demo 中只用于兼容场景

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_demo_app.py tests/test_workflow_graph_builder.py -q`
Expected: FAIL，提示 demo 主路径仍依赖 graph builder 判定。

**Step 3: Write minimal implementation**
- 暂不改生产代码，先确保测试精准表达目标。

**Step 4: Run targeted failure verification**
Run: `pytest tests/test_demo_app.py -q -k 'unified or builder'`
Expected: FAIL

---

### Task 2: 让 demo 顶层入口先做 goal 分类，不再先拿 full graph

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `tests/test_demo_app.py`

**Step 1: Write the failing test**
- `chat()` 对非 conversation 请求直接走 `AgentGraphRuntime`
- `stream_chat()` 对非 conversation 请求直接走 `AgentGraphRuntime`
- `chat()` / `stream_chat()` 对 conversation 请求走统一的 conversation graph 路径

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_demo_app.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 提供新的 goal-level 路由 helper
- `_compile_workflow()` 降级为兼容编译 helper 或移出主路径
- `_run_workflow()` / `stream_chat()` 改用 goal-level 主路径分流

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_demo_app.py -q`
Expected: PASS

---

### Task 3: 把 conversation 统一成 graph 语义，而不是 app 层特殊出口

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `tests/test_demo_app.py`

**Step 1: Write the failing test**
- conversation 请求最终 payload 带 `final_response`
- `stream_chat()` 的闲聊事件来自 conversation graph，而不是 app 层私有分支
- `final_response` 可消费 conversation 输出

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_demo_app.py -q -k 'conversation'`
Expected: FAIL

**Step 3: Write minimal implementation**
- 收敛 conversation payload 构造
- 让 `final_response` 能处理 conversation 场景的直接内容
- 保持流式体验，但不再以 app 层旁路语义为主

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_demo_app.py -q -k 'conversation'`
Expected: PASS

---

### Task 4: 将 `build_workflow_graph()` 降为真正兼容 API

**Files:**
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `tests/test_workflow_graph_builder.py`
- Modify: `tests/test_demo_app.py`

**Step 1: Write the failing test**
- demo 主路径不直接调用 `build_workflow_graph()`
- `build_workflow_graph()` 仅保留兼容标记与兼容行为
- legacy graph 拒绝逻辑仍可验证

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_demo_app.py tests/test_workflow_graph_builder.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 从 demo 主路径摘掉对 `build_workflow_graph()` 的直接依赖
- graph builder 保留 compat metadata 与 compat tests

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_demo_app.py tests/test_workflow_graph_builder.py -q`
Expected: PASS

---

### Task 5: 清理冗余路径与文档，完成统一收口

**Files:**
- Modify: `docs/当前Agent设计框架.md`
- Modify: `docs/plans/2026-04-02-unified-agent-graph-entry-implementation-plan.md`
- Modify: `docs/plans/2026-04-01-agent-graph-runtime-design.md`
- Modify: `tests/test_demo_app.py`

**Step 1: Write/update verification test**
- 断言主路径没有多余分支语义残留
- 检查必要 payload 仍完整

**Step 2: Run focused suites**
Run: `pytest tests/test_demo_app.py tests/test_workflow_runtime.py tests/test_workflow_graph_builder.py -q`
Expected: PASS

**Step 3: Update docs**
- 更新主入口说明
- 写明 conversation graph / agent graph 双主链
- 记录 `build_workflow_graph()` 的 compat-only 定位

**Step 4: Run full test suite**
Run: `pytest -q`
Expected: PASS

---

## 3. 风险与应对

### 风险 1：conversation 流式体验回退
**应对：** 保留 `stream_conversation_reply()` 作为 conversation 节点底层生成器，只改变其顶层语义归属。

### 风险 2：测试大量依赖 `build_workflow_graph()` monkeypatch
**应对：** 把这些测试改写为 patch goal-level routing helper，而不是 patch full graph compiler。

### 风险 3：主路径切换后 payload 回归
**应对：** 先锁定 `judge` / `planned_subgraphs` / `append_history` / `execution_trace` 回归测试，再改实现。

---

## 4. 最终验证矩阵

- `pytest tests/test_demo_app.py -q`
- `pytest tests/test_workflow_runtime.py -q`
- `pytest tests/test_workflow_graph_builder.py -q`
- `pytest -q`
