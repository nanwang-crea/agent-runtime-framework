# Agent Graph Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Finish the migration from a mixed workflow-plus-loop agent into a graph-first agent runtime where `WorkflowRuntime` is the only top-level execution path for workspace requests and `WorkspaceAgentLoop` remains only as a compatibility executor.

**Architecture:** Keep `WorkflowRuntime` as the execution kernel and continue routing non-conversation requests through graph compilation. Shrink the current `workspace_subtask -> WorkspaceAgentLoop` black box by promoting stable loop phases into first-class workflow nodes, then leave `WorkspaceSubtaskExecutor` as a narrow bridge for unsupported legacy flows.

**Tech Stack:** Python dataclasses, workflow runtime, demo app routing, workspace backend compatibility executor, pytest.

### Task 1: Lock the graph-first migration boundary
**Status:** completed


**Files:**
- Modify: `README.md`
- Modify: `docs/当前Agent设计框架.md`
- Modify: `docs/通用Agent.md`

**Step 1: Write the target migration notes**
- Record the architectural rule that `WorkflowRuntime` is the only top-level execution kernel for workspace requests.
- State that `WorkspaceAgentLoop` and `WorkspaceBackend` are compatibility executors, not product entry runtimes.
- Mark the current state as `partial migration complete`.

**Step 2: Add a current-vs-target table**
- Document which areas are already graph-native:
  - routing
  - graph build
  - approval / resume
  - aggregation
  - final response
- Document which areas are still loop-backed:
  - complex workspace subtask execution
  - clarification handling
  - tool-call orchestration fallback

**Step 3: Align naming in docs**
- Replace wording that implies the old loop is still the primary runtime.
- Keep references to `WorkspaceAgentLoop` only where compatibility behavior matters.

**Step 4: Verify docs read coherently**
Run:
```bash
rg -n "主运行时|top-level runtime|WorkspaceAgentLoop|WorkspaceBackend" README.md docs/当前Agent设计框架.md docs/通用Agent.md
```
Expected: Matches describe `WorkflowRuntime` as primary and loop as compatibility.

### Task 2: Add failing coverage for graph-only top-level routing
**Status:** completed


**Files:**
- Modify: `tests/test_demo_app.py`
- Modify: `tests/test_workflow_graph_builder.py`
- Modify: `tests/test_workflow_runtime.py`

**Step 1: Write the failing routing tests**
- Add a test proving non-chat workspace requests do not call `self.loop.run(...)` directly from `DemoAssistantApp.chat()` when workflow routing is applicable.
- Add a test proving a simple non-chat request returns a workflow payload even when it compiles to a single node graph.

**Step 2: Write the failing graph-builder tests**
- Add tests proving common workspace requests compile to explicit graph nodes instead of falling back to `workspace_subtask`.
- Start with at least:
  - repository overview request
  - single file read request
  - multi-file read-and-summarize request

**Step 3: Write the failing runtime tests**
- Add a test proving a single-node workflow still goes through `WorkflowRuntime` state transitions and run history fields.

**Step 4: Run targeted tests to confirm failure**
Run:
```bash
pytest tests/test_demo_app.py tests/test_workflow_graph_builder.py tests/test_workflow_runtime.py -q
```
Expected: FAIL on direct-loop fallback assumptions that are still present.

### Task 3: Remove app-level direct loop execution for workspace requests
**Status:** completed


**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the minimal routing change**
- Refactor `DemoAssistantApp.chat()` so conversation-style requests may still bypass workflow, but non-conversation workspace requests always produce a workflow run.
- Keep clarification behavior working, but move it behind workflow routing wherever possible.

**Step 2: Preserve compatibility behavior intentionally**
- If a request still requires legacy execution, compile a workflow graph that contains a `workspace_subtask` node rather than calling `self.loop.run(...)` directly in the app layer.

**Step 3: Run targeted tests**
Run:
```bash
pytest tests/test_demo_app.py -q
```
Expected: PASS

### Task 4: Expand graph-builder coverage for explicit native nodes
**Status:** completed

**Files:**
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Modify: `tests/test_workflow_graph_builder.py`

**Step 1: Add explicit node-shape rules**
- Teach graph compilation to prefer explicit native nodes for requests already supported by the runtime.
- Keep `workspace_subtask` only for unsupported or genuinely complex flows.

**Step 2: Add stable executor metadata**
- Ensure compiled nodes carry executor metadata consistently so the runtime and UI can explain why a node is native or loop-backed.

**Step 3: Tighten fallback criteria**
- Document and encode when fallback is allowed.
- Examples:
  - unsupported edit workflows
  - legacy multi-step codex tasks
  - compatibility-only resume paths

**Step 4: Run focused tests**
Run:
```bash
pytest tests/test_workflow_graph_builder.py -q
```
Expected: PASS

### Task 5: Promote the first loop phases into graph nodes
**Status:** completed

**Files:**
- Create: `agent_runtime_framework/workflow/tool_call_executor.py`
- Create: `agent_runtime_framework/workflow/clarification_executor.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `tests/test_workflow_runtime.py`

**Step 1: Write failing executor tests**
- Add tests for a `tool_call` node executor that invokes one registered tool and returns normalized node output.
- Add tests for a `clarification` node executor that returns a workflow-level clarification result without dropping out of graph execution.

**Step 2: Implement the minimal `tool_call` node**
- Accept tool name and arguments from node metadata.
- Execute through existing tool registry access in application context.
- Normalize tool output into `NodeResult.output`.

**Step 3: Implement the minimal `clarification` node**
- Produce a consistent pending/needs-input result representation.
- Keep approval and resume semantics separate from clarification semantics.

**Step 4: Register new executors in workflow runtime composition**
- Update `DemoAssistantApp._build_workflow_runtime()` to include these executors.

**Step 5: Run focused tests**
Run:
```bash
pytest tests/test_workflow_runtime.py -q
```
Expected: PASS

### Task 6: Shrink `WorkspaceSubtaskExecutor` into a documented bridge
**Status:** completed

**Files:**
- Modify: `agent_runtime_framework/workflow/workspace_subtask.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `docs/当前Agent设计框架.md`
- Modify: `tests/test_workflow_codex_subtask.py`

**Step 1: Add fallback reason metadata**
- Record why a node is using `workspace_subtask`.
- Include fields such as:
  - `fallback_reason`
  - `compatibility_mode`
  - `source_loop`

**Step 2: Limit bridge responsibilities**
- Ensure the executor only runs goal strings that cannot yet be represented with explicit nodes.
- Avoid adding new product features inside the bridge executor.

**Step 3: Add tests for bridge boundaries**
- Verify the executor still supports legacy resume and result conversion.
- Verify native-capable requests no longer compile into this executor.

**Step 4: Run targeted tests**
Run:
```bash
pytest tests/test_workflow_codex_subtask.py tests/test_workflow_graph_builder.py -q
```
Expected: PASS

### Task 7: Normalize verification and approval as graph-native policies
**Status:** completed

**Files:**
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Modify: `agent_runtime_framework/workflow/runtime.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `tests/test_workflow_runtime.py`
- Modify: `tests/test_workflow_approval.py`

**Step 1: Write the failing policy tests**
- Add tests proving change-oriented requests automatically insert verification nodes.
- Add tests proving high-risk requests insert approval gates even when execution eventually falls back to `workspace_subtask`.

**Step 2: Implement graph-native insertion rules**
- Keep approval and verification attached to graph structure, not app-layer conditionals.

**Step 3: Verify resume behavior**
- Ensure resumed runs continue through `WorkflowRuntime.resume()` rather than custom app-specific shortcuts.

**Step 4: Run focused tests**
Run:
```bash
pytest tests/test_workflow_runtime.py tests/test_workflow_approval.py -q
```
Expected: PASS

### Task 8: Add regression coverage for end-to-end graph-first behavior
**Status:** completed

**Files:**
- Modify: `tests/test_demo_app.py`
- Modify: `tests/test_workflow_runtime.py`
- Modify: `tests/test_workflow_graph_builder.py`

**Step 1: Add end-to-end regression tests**
- Cover these scenarios:
  - conversation request stays on conversation path
  - repository question runs through workflow path
  - simple file read runs through workflow path
  - unsupported complex task compiles to `workspace_subtask` but still executes inside workflow runtime
  - approval / resume still works after graph-first routing cleanup

**Step 2: Run the focused regression suite**
Run:
```bash
pytest tests/test_demo_app.py tests/test_workflow_graph_builder.py tests/test_workflow_runtime.py tests/test_workflow_approval.py tests/test_workflow_codex_subtask.py -q
```
Expected: PASS

### Task 9: Clean up dead loop-first assumptions
**Status:** completed

**Files:**
- Modify: `README.md`
- Modify: `docs/当前Agent设计框架.md`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/agents/workspace_backend/__init__.py`

**Step 1: Search for stale assumptions**
Run:
```bash
rg -n "loop-first|CodexAgentLoop = 主运行时|WorkspaceBackend = 主运行时|self\.loop\.run\(" README.md docs agent_runtime_framework
```
Expected: Any remaining matches are either compatibility notes or true dead-code candidates.

**Step 2: Remove or rewrite stale references**
- Keep compatibility aliases only if public imports still depend on them.
- Do not delete compatibility exports until tests prove they are unused or intentionally retained.

**Step 3: Run final targeted verification**
Run:
```bash
pytest tests/test_demo_app.py tests/test_workflow_graph_builder.py tests/test_workflow_runtime.py tests/test_workflow_approval.py tests/test_workflow_codex_subtask.py -q
```
Expected: PASS

### Task 10: Summarize migration status and remaining follow-up
**Status:** completed

**Files:**
- Modify: `README.md`
- Modify: `docs/当前Agent设计框架.md`

**Step 1: Add a migration status section**
- Mark what is now graph-native.
- Mark what still intentionally uses compatibility fallback.
- Record the next likely follow-ups:
  - richer node taxonomy
  - parallel scheduling
  - model-planned graphs
  - subagent nodes

**Step 2: Verify documentation matches code reality**
Run:
```bash
rg -n "workspace_subtask|WorkflowRuntime|compatibility" README.md docs/当前Agent设计框架.md
```
Expected: Docs clearly explain the mixed-but-graph-first state.
