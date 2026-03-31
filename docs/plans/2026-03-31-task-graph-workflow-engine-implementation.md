# Task Graph / Workflow Engine Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current single-task `CodexAgentLoop` top-level runtime with a graph-driven workflow runtime that can decompose compound goals, execute node-level tasks, support approval/recovery, and aggregate final answers with references.

**Architecture:** Introduce a new `agent_runtime_framework.workflow` package as the single top-level runtime. Keep the current `agent_runtime_framework.agents.codex` package as a compatibility subtask executor during migration, then gradually move high-frequency behaviors into native workflow nodes. Preserve the existing tool/runtime/resource/memory/model infrastructure where it already matches the target architecture.

**Tech Stack:** Python 3.10+, `dataclasses`, existing `ToolRegistry` / `execute_tool_call`, existing Codex agent modules, `pytest`.

### Task 1: Add workflow domain models

**Files:**
- Create: `agent_runtime_framework/workflow/__init__.py`
- Create: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_workflow_models.py`

**Step 1: Write the failing test**

Add tests that assert:

- `WorkflowRun` can hold graph, node states, and shared state
- `WorkflowNode` supports dependency metadata and execution policy fields
- `NodeState` and `NodeResult` capture result/error/approval data cleanly
- `WorkflowRun` and `WorkflowNode` expose stable status values

```python
def test_workflow_run_tracks_graph_and_node_states():
    run = WorkflowRun(goal="read README and summarize")
    assert run.status == "pending"
    assert run.shared_state == {}
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_models.py -v`
Expected: FAIL with import or name errors because the workflow model package does not exist yet.

**Step 3: Write minimal implementation**

Implement:

- `WorkflowRun`
- `WorkflowGraph`
- `WorkflowNode`
- `WorkflowEdge`
- `NodeState`
- `NodeResult`
- stable status constants or literal-friendly string values

Keep the implementation small and dataclass-based.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/__init__.py agent_runtime_framework/workflow/models.py tests/test_workflow_models.py
git commit -m "feat: add workflow runtime domain models"
```

### Task 2: Add workflow scheduler and minimal runtime loop

**Files:**
- Create: `agent_runtime_framework/workflow/runtime.py`
- Create: `agent_runtime_framework/workflow/scheduler.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1: Write the failing test**

Add tests that assert:

- runtime can execute a graph with one ready node and one finish node
- scheduler only runs nodes whose dependencies are completed
- failed node stops downstream execution
- completed run transitions to `completed`

```python
def test_runtime_executes_ready_nodes_in_dependency_order():
    run = build_simple_run()
    result = WorkflowRuntime(executors={"noop": NoopExecutor()}).run(run)
    assert result.status == "completed"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_runtime.py -v`
Expected: FAIL because the runtime and scheduler are not implemented.

**Step 3: Write minimal implementation**

Implement:

- `WorkflowScheduler.ready_nodes()`
- `WorkflowRuntime.run()`
- sequential execution only for the first version
- node state transitions: `pending -> running -> completed/failed`

No parallelism yet.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_runtime.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/runtime.py agent_runtime_framework/workflow/scheduler.py agent_runtime_framework/workflow/__init__.py tests/test_workflow_runtime.py
git commit -m "feat: add minimal workflow runtime and scheduler"
```

### Task 3: Add goal analysis and decomposition layer

**Files:**
- Create: `agent_runtime_framework/workflow/goal_analysis.py`
- Create: `agent_runtime_framework/workflow/decomposition.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_workflow_decomposition.py`

**Step 1: Write the failing test**

Add tests that assert:

- a simple file-read request becomes a single subtask
- a compound request becomes multiple subtasks
- directory + README requests decompose into repository overview + file read + synthesis

```python
def test_compound_request_decomposes_into_multiple_subtasks():
    goal = analyze_goal("帮我列一下当前文件夹都有什么，以及读取一下README文件并总结告诉我在讲什么")
    subtasks = decompose_goal(goal)
    assert [item.task_profile for item in subtasks] == ["repository_explainer", "file_reader", "final_synthesis"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_decomposition.py -v`
Expected: FAIL because analysis/decomposition logic does not exist yet.

**Step 3: Write minimal implementation**

Implement:

- `GoalSpec`
- `SubTaskSpec`
- `analyze_goal()`
- `decompose_goal()`

Prefer a model-first hook if available, but keep a deterministic fallback for testability.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_decomposition.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/goal_analysis.py agent_runtime_framework/workflow/decomposition.py agent_runtime_framework/workflow/models.py tests/test_workflow_decomposition.py
git commit -m "feat: add workflow goal analysis and decomposition"
```

### Task 4: Add graph builder for single and compound goals

**Files:**
- Create: `agent_runtime_framework/workflow/graph_builder.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing test**

Add tests that assert:

- simple file-read request builds a small graph
- compound read/list request builds multiple nodes and edges
- aggregate/final nodes are inserted automatically
- modification requests insert verification nodes when needed

```python
def test_graph_builder_adds_aggregate_and_final_nodes_for_compound_goal():
    graph = build_workflow_graph(example_compound_goal())
    assert any(node.node_type == "aggregate_results" for node in graph.nodes)
    assert any(node.node_type == "final_response" for node in graph.nodes)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_graph_builder.py -v`
Expected: FAIL because graph builder does not exist yet.

**Step 3: Write minimal implementation**

Implement deterministic graph compilation:

- `GoalSpec -> WorkflowGraph`
- dependency edges
- insertion of `aggregate_results`
- insertion of `final_response`
- insertion of `verification` for change flows

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_graph_builder.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/graph_builder.py agent_runtime_framework/workflow/models.py tests/test_workflow_graph_builder.py
git commit -m "feat: add workflow graph builder"
```

### Task 5: Add native node executor interface and simple read/overview executors

**Files:**
- Create: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/workflow/runtime.py`
- Test: `tests/test_workflow_node_executors.py`

**Step 1: Write the failing test**

Add tests that assert:

- `workspace_overview` node can produce directory evidence
- `file_read` node can resolve and summarize file content
- node executors return `NodeResult` with references/evidence

```python
def test_file_read_executor_reads_readme_and_returns_references(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Demo\nhello\n", encoding="utf-8")
    result = FileReadExecutor().execute(node, run, context)
    assert "README.md" in result.references[0]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_node_executors.py -v`
Expected: FAIL because native node executors do not exist.

**Step 3: Write minimal implementation**

Implement:

- `NodeExecutor` protocol
- `WorkspaceOverviewExecutor`
- `FileReadExecutor`
- small adapter helpers that reuse existing `resources` and `tools` behavior where practical

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_node_executors.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/node_executors.py agent_runtime_framework/workflow/runtime.py tests/test_workflow_node_executors.py
git commit -m "feat: add native workflow read and overview executors"
```

### Task 6: Add codex compatibility subtask executor

**Files:**
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Create: `agent_runtime_framework/workflow/codex_subtask.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Test: `tests/test_workflow_codex_subtask.py`

**Step 1: Write the failing test**

Add tests that assert:

- a workflow node can delegate to `CodexAgentLoop`
- codex subtask output is converted into `NodeResult`
- existing single-task behavior can still be reused under workflow runtime

```python
def test_codex_subtask_executor_wraps_codex_loop_result(tmp_path):
    result = CodexSubtaskExecutor().execute(node, run, context)
    assert result.summary
    assert result.evidence_items
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_codex_subtask.py -v`
Expected: FAIL because the compatibility executor does not exist.

**Step 3: Write minimal implementation**

Implement:

- `CodexSubtaskExecutor`
- adapter from `CodexAgentLoopResult` to `NodeResult`
- minimal stable API for invoking codex loop on one subtask goal

Avoid changing core single-task behavior unless needed for clean reuse.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_codex_subtask.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/codex_subtask.py agent_runtime_framework/workflow/node_executors.py agent_runtime_framework/agents/codex/loop.py tests/test_workflow_codex_subtask.py
git commit -m "feat: add codex compatibility subtask executor"
```

### Task 7: Add aggregation and final-response executors

**Files:**
- Create: `agent_runtime_framework/workflow/aggregator.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Test: `tests/test_workflow_aggregator.py`

**Step 1: Write the failing test**

Add tests that assert:

- multiple node outputs can be aggregated into one shared result
- final response contains merged summaries and references
- final response does not skip required subtask evidence

```python
def test_aggregator_merges_subtask_results_with_references():
    result = aggregate_results([overview_result, readme_result])
    assert "README.md" in "\n".join(result.references)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_aggregator.py -v`
Expected: FAIL because no workflow aggregator exists.

**Step 3: Write minimal implementation**

Implement:

- `aggregate_node_results()`
- `AggregationExecutor`
- `FinalResponseExecutor`

The output should produce concise synthesis plus stable references.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_aggregator.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/aggregator.py agent_runtime_framework/workflow/node_executors.py tests/test_workflow_aggregator.py
git commit -m "feat: add workflow aggregation and final response executors"
```

### Task 8: Add node-level approval and resume support

**Files:**
- Create: `agent_runtime_framework/workflow/approval.py`
- Modify: `agent_runtime_framework/workflow/runtime.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_workflow_approval.py`

**Step 1: Write the failing test**

Add tests that assert:

- a high-risk node can pause in `waiting_approval`
- approval resumes only the paused node
- the run completes after approval without restarting earlier completed nodes

```python
def test_workflow_runtime_resumes_only_waiting_approval_node():
    runtime = WorkflowRuntime(...)
    first = runtime.run(run)
    resumed = runtime.resume(first.resume_token, approved=True)
    assert resumed.status == "completed"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_approval.py -v`
Expected: FAIL because workflow approval state handling does not exist.

**Step 3: Write minimal implementation**

Implement:

- node-level approval record shape
- runtime pause/resume entrypoints
- node-level transition from `waiting_approval` back to `ready/running`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_approval.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/approval.py agent_runtime_framework/workflow/runtime.py agent_runtime_framework/workflow/models.py tests/test_workflow_approval.py
git commit -m "feat: add workflow node approval and resume"
```

### Task 9: Add workflow persistence for restart recovery

**Files:**
- Create: `agent_runtime_framework/workflow/persistence.py`
- Modify: `agent_runtime_framework/workflow/runtime.py`
- Test: `tests/test_workflow_persistence.py`

**Step 1: Write the failing test**

Add tests that assert:

- workflow run state can be serialized and restored
- node states survive process restart simulation
- approval/clarification waits survive restart

```python
def test_workflow_run_can_restore_waiting_approval_state(tmp_path):
    store = WorkflowPersistenceStore(tmp_path / "workflow.json")
    store.save(run)
    restored = store.load(run.run_id)
    assert restored.status == "waiting_approval"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_persistence.py -v`
Expected: FAIL because persistence store does not exist.

**Step 3: Write minimal implementation**

Implement:

- file-backed workflow persistence store
- serialization for graph, node states, shared state, pending waits
- load by run id

Prefer JSON and workspace-local persistence first.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_persistence.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/persistence.py agent_runtime_framework/workflow/runtime.py tests/test_workflow_persistence.py
git commit -m "feat: add workflow persistence for recovery"
```

### Task 10: Route demo app through workflow runtime

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/__init__.py`
- Modify: `README.md`
- Test: `tests/test_demo_app.py`
- Test: `tests/test_workflow_end_to_end.py`

**Step 1: Write the failing test**

Add tests that assert:

- demo app now uses workflow runtime as the default execution path
- compound request creates multiple workflow nodes
- final answer contains both directory overview and README summary

```python
def test_demo_app_routes_compound_goal_through_workflow_runtime(tmp_path):
    payload = app.chat("帮我列一下当前文件夹都有什么，以及读取一下README文件并总结告诉我在讲什么")
    assert payload["status"] == "completed"
    assert "README" in payload["final_answer"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_demo_app.py tests/test_workflow_end_to_end.py -v`
Expected: FAIL because `DemoAssistantApp` still drives `CodexAgentLoop` directly.

**Step 3: Write minimal implementation**

Modify:

- `create_demo_assistant_app()` to create `WorkflowRuntime`
- `DemoAssistantApp.chat()` and `stream_chat()` to invoke workflow runtime
- export the new workflow runtime surface where needed
- update `README.md` architecture section to reflect the new primary runtime

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_demo_app.py tests/test_workflow_end_to_end.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/demo/app.py agent_runtime_framework/__init__.py README.md tests/test_demo_app.py tests/test_workflow_end_to_end.py
git commit -m "feat: route demo app through workflow runtime"
```

### Task 11: Remove or demote obsolete single-task top-level paths

**Files:**
- Modify: `agent_runtime_framework/agents/codex/__init__.py`
- Modify: `agent_runtime_framework/agents/__init__.py`
- Modify: `agent_runtime_framework/__init__.py`
- Modify: `docs/当前Agent设计框架.md`
- Modify: `docs/当前进展与改进建议.md`
- Test: `tests/test_workflow_end_to_end.py`

**Step 1: Write the failing test**

Add tests that assert:

- workflow runtime is the top-level active runtime surface
- codex loop remains available only as compatibility execution backend

```python
def test_public_surface_marks_workflow_runtime_as_primary():
    import agent_runtime_framework as arf
    assert hasattr(arf, "WorkflowRuntime")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_end_to_end.py -k public_surface -v`
Expected: FAIL because top-level exports and docs still center the codex loop.

**Step 3: Write minimal implementation**

Update:

- top-level exports
- package docs
- current-architecture docs
- de-emphasize old single-task primary path

Delete clearly obsolete glue only after replacement is proven.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_end_to_end.py -k public_surface -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/agents/codex/__init__.py agent_runtime_framework/agents/__init__.py agent_runtime_framework/__init__.py docs/当前Agent设计框架.md docs/当前进展与改进建议.md tests/test_workflow_end_to_end.py
git commit -m "refactor: promote workflow runtime as primary surface"
```

### Task 12: Run full regression and clean dead code

**Files:**
- Modify: `README.md`
- Modify: `docs/2026-03-31-TaskGraph工作流引擎重构设计方案.md`
- Modify: `docs/2026-03-31-TaskGraph工作流引擎重构设计方案.md`
- Modify: only files required by cleanup

**Step 1: Run focused workflow tests**

Run: `pytest tests/test_workflow_models.py tests/test_workflow_runtime.py tests/test_workflow_decomposition.py tests/test_workflow_graph_builder.py tests/test_workflow_node_executors.py tests/test_workflow_codex_subtask.py tests/test_workflow_aggregator.py tests/test_workflow_approval.py tests/test_workflow_persistence.py tests/test_workflow_end_to_end.py -v`
Expected: PASS

**Step 2: Run relevant existing regression tests**

Run: `pytest tests/test_codex_agent.py tests/test_demo_app.py tests/test_memory_and_policy.py tests/test_tool_registry.py -v`
Expected: PASS or only known unrelated failures

**Step 3: Remove dead compatibility code if now unused**

Candidates to inspect:

- legacy top-level codex-only wiring in `agent_runtime_framework/demo/app.py`
- obsolete single-task routing shortcuts that bypass workflow runtime
- stale docs that still claim `CodexAgentLoop` is the primary runtime

**Step 4: Re-run full validation**

Run: `pytest -v`
Expected: PASS or a short list of known pre-existing unrelated failures documented explicitly

**Step 5: Commit**

```bash
git add README.md docs/2026-03-31-TaskGraph工作流引擎重构设计方案.md docs/plans/2026-03-31-task-graph-workflow-engine-implementation.md agent_runtime_framework tests
git commit -m "refactor: complete workflow runtime migration"
```

## Notes for Execution

- Keep the first working vertical slice small: sequential scheduler, simple graph builder, two native node types, one compatibility codex node.
- Do not introduce broad generic abstractions before at least two real node executors need them.
- Preserve the existing tool/runtime/resource layers unless a concrete test shows a structural mismatch.
- Prefer deleting obsolete direct codex-primary wiring once the workflow path is proven by end-to-end tests.
- When in doubt, favor explicit graph/node state over hidden planner state.

## Recommended Implementation Order

1. Task 1–4: get the graph domain and compilation path stable
2. Task 5–7: get node execution and aggregation stable
3. Task 8–9: add node-level recovery and persistence
4. Task 10–12: switch the app entrypoint and remove obsolete top-level wiring
