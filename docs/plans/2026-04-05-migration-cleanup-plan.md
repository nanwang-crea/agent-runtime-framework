# Migration Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove migration-period leftovers so the graph-first runtime is the only clear execution model, with compatibility paths explicitly bounded, validated, and easy to delete later.

**Architecture:** Keep the current workflow-first direction, but finish the boundary cleanup in the places where the code still behaves like a hybrid runtime. The cleanup should focus on four concrete debt buckets already visible in the codebase: bridge logic owned by `DemoAssistantApp`, scheduler bypasses inside `AgentGraphRuntime`, overloaded runtime responsibilities inside `agent_graph_runtime.py`, and reverse dependencies from `workflow` back into `agents.workspace_backend`. Every step should be test-led, preserve public behavior unless the step explicitly narrows it, and make the remaining compatibility layer smaller and easier to reason about.

**Tech Stack:** Python 3.10+, dataclasses, pytest, Electron/React shell, local file-backed persistence

### Task 1: Freeze the migration target and debt inventory

**Status:** completed on 2026-04-05

**Files:**
- Modify: `README.md`
- Modify: `docs/当前Agent设计框架.md`
- Create: `docs/plans/2026-04-05-migration-cleanup-audit.md`
- Test: `tests/test_public_surface.py`

**Step 1: Record the current debt inventory**

Write the audit document with these concrete debt categories:

- `agent_runtime_framework/workflow/agent_graph_runtime.py` still owns orchestration, state restoration, system-node materialization, and finalization logic together
- `agent_runtime_framework/demo/app.py` still owns compatibility subtask result assembly
- `AgentGraphRuntime` still bypasses scheduler-driven execution by calling `workflow_runtime._execute(...)` directly
- parts of `agent_runtime_framework/workflow` still depend on `agent_runtime_framework/agents/workspace_backend/*`
- bridge-path tests do not currently protect the `DemoAssistantApp._run_workspace_subtask(... target_path=...)` failure mode

**Step 2: Verify current public-surface coverage**

Run: `pytest tests/test_public_surface.py -v`
Expected: PASS, but no assertion should yet freeze the migration debt inventory.

**Step 3: Update the docs to make the target explicit**

Make the docs state these rules plainly:

- `RootGraphRuntime` routes, but does not own business logic.
- `AgentGraphRuntime` owns iterative graph orchestration only.
- `GraphExecutionRuntime` owns node scheduling and node execution only.
- compatibility bridge executors are transitional and must not contain app-specific logic.
- direct executor calls outside scheduler/runtime are temporary debt to remove.
- `workflow` must not depend on `agents.workspace_backend` for planner-time parsing or prompt assembly once cleanup is complete.

**Step 4: Add a narrow public-surface assertion only if stable**

If there is a wording or export guarantee worth freezing, add a very small assertion. Do not overfit tests to documentation prose.

**Step 5: Commit**

```bash
git add README.md docs/当前Agent设计框架.md docs/plans/2026-04-05-migration-cleanup-audit.md tests/test_public_surface.py
git commit -m "docs: define migration cleanup target"
```

### Task 2: Cover and fix the immediate bridge-path correctness bug

**Status:** completed on 2026-04-05

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `tests/test_demo_app.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing regression test**

Add a test that directly exercises:

- `DemoAssistantApp._run_workspace_subtask()`
- with `metadata={"target_path": "README.md"}`
- and asserts the result includes target evidence instead of raising `NameError`

Also assert that bridge metadata still includes the summary and task profile.

**Step 2: Run the targeted test to confirm failure**

Run: `pytest tests/test_demo_app.py -v`
Expected: FAIL with `NameError: name 'EvidenceItem' is not defined` or an equivalent bridge-path error.

**Step 3: Write the minimal implementation**

Fix the missing import or equivalent local bug in `agent_runtime_framework/demo/app.py`. Keep the change minimal and local; do not refactor ownership yet.

**Step 4: Run the targeted tests**

Run: `pytest tests/test_demo_app.py tests/test_workflow_codex_subtask.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/demo/app.py tests/test_demo_app.py tests/test_workflow_codex_subtask.py
git commit -m "fix: cover demo bridge target evidence path"
```

### Task 3: Move compatibility subtask execution out of the demo app

**Status:** completed on 2026-04-05

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/demo/runtime_factory.py`
- Create: `agent_runtime_framework/demo/compat_subtask_runner.py`
- Modify: `agent_runtime_framework/workflow/workspace_subtask.py`
- Test: `tests/test_demo_app.py`
- Test: `tests/test_workflow_codex_subtask.py`

**Step 1: Write the failing tests**

Add tests that prove:

- the compatibility subtask runner can be constructed without `DemoAssistantApp`
- `DemoRuntimeFactory` injects a runner object instead of binding `self.app._run_workspace_subtask`
- `DemoAssistantApp` no longer assembles `WorkspaceTask` / `EvidenceItem` bridge payloads itself

**Step 2: Run the focused tests to confirm failure**

Run: `pytest tests/test_demo_app.py tests/test_workflow_codex_subtask.py -v`
Expected: FAIL because the bridge logic is still embedded in `DemoAssistantApp` and wired through `DemoRuntimeFactory`.

**Step 3: Extract a dedicated runner**

Create `agent_runtime_framework/demo/compat_subtask_runner.py` with one focused responsibility:

- produce `WorkspaceSubtaskResult` for compatibility-mode subtasks
- preserve current `target_path` evidence behavior
- keep any future delegation to the real compatibility backend behind this boundary

**Step 4: Wire the factory and slim the app**

Update `DemoRuntimeFactory` to construct and inject the runner into `WorkspaceSubtaskExecutor`. Remove `_run_workspace_subtask()` from `DemoAssistantApp` or reduce it to a trivial passthrough only if the tests still need a compatibility shim temporarily.

**Step 5: Run the focused tests**

Run: `pytest tests/test_demo_app.py tests/test_workflow_codex_subtask.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add agent_runtime_framework/demo/app.py agent_runtime_framework/demo/runtime_factory.py agent_runtime_framework/demo/compat_subtask_runner.py agent_runtime_framework/workflow/workspace_subtask.py tests/test_demo_app.py tests/test_workflow_codex_subtask.py
git commit -m "refactor: isolate compatibility subtask runner"
```

### Task 4: Split `AgentGraphRuntime` into orchestration vs. support services

**Status:** completed on 2026-04-05

**Files:**
- Modify: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Create: `agent_runtime_framework/workflow/system_node_manager.py`
- Create: `agent_runtime_framework/workflow/agent_graph_state_store.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_end_to_end.py`

**Step 1: Write the failing tests**

Add tests that exercise these responsibilities separately:

- restoring agent-graph state from persisted payload
- restoring a persisted `WorkflowRun` with approval metadata
- materializing aggregate / evidence / judge / final nodes without inlining all logic into `AgentGraphRuntime`
- clarification-related run updates without bundling every concern into one class

**Step 2: Run the targeted tests to confirm failure**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_end_to_end.py -v`
Expected: FAIL because those behaviors are still bundled in `AgentGraphRuntime`.

**Step 3: Extract state restoration**

Move `_restore_state()` and `_restore_workflow_run()` behavior into a focused helper or service with a narrow API.

**Step 4: Extract system-node materialization**

Move the logic behind:

- `_seed_system_nodes()`
- `_materialize_iteration_system_nodes()`
- result shaping for judge / finalize preparation

into a dedicated helper with explicit inputs and outputs.

**Step 5: Slim `AgentGraphRuntime`**

Leave `AgentGraphRuntime` owning only:

- iteration loop
- planner invocation
- judge invocation
- subrun consumption
- approval / completion handoff

**Step 6: Run the targeted tests**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_end_to_end.py -v`
Expected: PASS

**Step 7: Commit**

```bash
git add agent_runtime_framework/workflow/agent_graph_runtime.py agent_runtime_framework/workflow/system_node_manager.py agent_runtime_framework/workflow/agent_graph_state_store.py tests/test_workflow_runtime.py tests/test_workflow_end_to_end.py
git commit -m "refactor: split agent graph orchestration responsibilities"
```

### Task 5: Remove direct executor bypasses from steady-state graph flow

**Status:** completed on 2026-04-05

**Files:**
- Modify: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Modify: `agent_runtime_framework/workflow/execution_runtime.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/workflow/clarification_executor.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_end_to_end.py`

**Step 1: Write the failing tests**

Add tests that assert:

- clarification is represented as a graph-executed node, not only as a direct executor call
- final response is produced through normal graph execution in the steady-state path
- `AgentGraphRuntime` does not depend on `workflow_runtime._execute()` for clarification, evidence synthesis, or final response during normal orchestration

**Step 2: Run the targeted tests to confirm failure**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_end_to_end.py -v`
Expected: FAIL because current flow still manually invokes executor methods.

**Step 3: Update graph orchestration**

Represent these control-flow steps as graph nodes and let `GraphExecutionRuntime` execute them through the scheduler. If one fallback bypass must remain temporarily, mark it with a debt comment and a test that narrows its allowed scope.

**Step 4: Run the targeted tests**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_end_to_end.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/agent_graph_runtime.py agent_runtime_framework/workflow/execution_runtime.py agent_runtime_framework/workflow/node_executors.py agent_runtime_framework/workflow/clarification_executor.py tests/test_workflow_runtime.py tests/test_workflow_end_to_end.py
git commit -m "refactor: route system nodes through graph execution"
```

### Task 6: Remove reverse dependencies from `workflow` to `agents.workspace_backend`

**Status:** completed on 2026-04-05

**Files:**
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Modify: `agent_runtime_framework/workflow/decomposition.py`
- Modify: `agent_runtime_framework/workflow/goal_analysis.py`
- Modify: `agent_runtime_framework/workflow/llm_access.py`
- Modify: `agent_runtime_framework/workflow/conversation.py`
- Create or modify: a small workflow-local prompt/parsing helper module if needed
- Test: `tests/test_workflow_graph_builder.py`
- Test: `tests/test_workflow_decomposition.py`
- Test: `tests/test_workflow_models.py`

**Step 1: Write the failing tests**

Add tests that protect:

- JSON block extraction used by workflow planner/analyzer code without importing from `agents.workspace_backend`
- conversation prompt assembly still working after moving helper ownership
- workflow modules no longer importing `agent_runtime_framework.agents.workspace_backend.*` for planner-time parsing helpers

**Step 2: Run the focused tests to confirm failure**

Run: `pytest tests/test_workflow_graph_builder.py tests/test_workflow_decomposition.py tests/test_workflow_models.py -v`
Expected: FAIL because the workflow layer still imports parsing/prompt helpers from `agents.workspace_backend`.

**Step 3: Move or duplicate only the minimal helper surface**

Create a workflow-local helper for prompt parsing / prompt assembly if needed. Keep it tiny and avoid introducing a broad shared abstraction just to satisfy layering purity.

**Step 4: Update imports**

Move `workflow` modules off `agents.workspace_backend.prompting` and `agents.workspace_backend.run_context` where the dependency is only for workflow-owned behavior.

**Step 5: Run the focused tests**

Run: `pytest tests/test_workflow_graph_builder.py tests/test_workflow_decomposition.py tests/test_workflow_models.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add agent_runtime_framework/workflow/graph_builder.py agent_runtime_framework/workflow/decomposition.py agent_runtime_framework/workflow/goal_analysis.py agent_runtime_framework/workflow/llm_access.py agent_runtime_framework/workflow/conversation.py tests/test_workflow_graph_builder.py tests/test_workflow_decomposition.py tests/test_workflow_models.py
git commit -m "refactor: remove workflow dependency on workspace backend helpers"
```

### Task 7: Make compatibility fallback explicit, minimal, and measurable

**Status:** completed on 2026-04-05

**Files:**
- Modify: `agent_runtime_framework/workflow/subgraph_planner.py`
- Modify: `agent_runtime_framework/workflow/workspace_subtask.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Test: `tests/test_workflow_codex_subtask.py`
- Test: `tests/test_workflow_decomposition.py`

**Step 1: Write the failing tests**

Add tests for:

- when compatibility fallback is chosen
- what metadata must be attached: `fallback_reason`, `compatibility_mode`, and `source_loop` when applicable
- a case where graph-native nodes are preferred and fallback is not selected

**Step 2: Run the targeted tests to confirm failure**

Run: `pytest tests/test_workflow_codex_subtask.py tests/test_workflow_decomposition.py -v`
Expected: FAIL where fallback selection is currently implicit or only weakly asserted.

**Step 3: Tighten fallback contracts**

Make fallback selection and metadata explicit in planner and executor outputs. Prefer existing metadata shapes and avoid a large new abstraction.

**Step 4: Run the targeted tests**

Run: `pytest tests/test_workflow_codex_subtask.py tests/test_workflow_decomposition.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/subgraph_planner.py agent_runtime_framework/workflow/workspace_subtask.py agent_runtime_framework/workflow/models.py tests/test_workflow_codex_subtask.py tests/test_workflow_decomposition.py
git commit -m "test: freeze compatibility fallback contract"
```

### Task 8: Clean dead code and obsolete migration language

**Status:** completed on 2026-04-05

**Files:**
- Modify: `agent_runtime_framework/__init__.py`
- Modify: `README.md`
- Modify: `docs/当前Agent设计框架.md`
- Modify: any now-unused runtime or demo module discovered during Tasks 2-7
- Test: `tests/test_public_surface.py`

**Step 1: Identify dead or misleading code**

Look for:

- helpers no longer called after extraction
- comments or docs that still blur `WorkflowRuntime`, `GraphExecutionRuntime`, and `AgentGraphRuntime`
- compatibility shims left behind in `DemoAssistantApp` after the runner extraction
- exports kept only for migration notes but not needed by code or tests

**Step 2: Remove dead code in small slices**

Delete only code proven unused by search plus tests. Do not remove compatibility code still used by planner or runtime.

**Step 3: Run surface tests**

Run: `pytest tests/test_public_surface.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add agent_runtime_framework/__init__.py README.md docs/当前Agent设计框架.md tests/test_public_surface.py
git commit -m "chore: remove obsolete migration leftovers"
```

### Task 9: Add verification coverage for the cleaned architecture

**Status:** completed on 2026-04-05

**Files:**
- Modify: `tests/test_workflow_runtime.py`
- Modify: `tests/test_workflow_approval.py`
- Modify: `tests/test_workflow_end_to_end.py`
- Modify: `tests/test_demo_app.py`

**Step 1: Add the missing regression tests**

Cover:

- approval resume after persisted agent-graph state restoration
- clarification across turns and after restart
- graph-native final response path
- compatibility fallback path still functioning after cleanup
- failure behavior when a planner emits an unsupported node type
- the bridge runner preserving target evidence after moving out of the demo app

**Step 2: Run the focused suite**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_approval.py tests/test_workflow_end_to_end.py tests/test_demo_app.py -v`
Expected: PASS

**Step 3: Run the full suite**

Run: `pytest -q`
Expected: PASS

**Step 4: Commit**

```bash
git add tests/test_workflow_runtime.py tests/test_workflow_approval.py tests/test_workflow_end_to_end.py tests/test_demo_app.py
git commit -m "test: lock down cleaned graph runtime behavior"
```

### Task 10: Final verification and closeout

**Status:** completed on 2026-04-05

**Files:**
- No required code changes

**Step 1: Run repository verification**

Run: `pytest -q`
Expected: PASS

**Step 2: Manually smoke-test representative flows**

Run:

```bash
python -m agent_runtime_framework.demo.server --workspace .
```

Check:

- normal conversation request
- repository overview request
- file read request
- clarification request and follow-up
- approval / resume path if available
- one compatibility fallback request that still legitimately uses the bridge path

**Step 3: Summarize remaining debt**

If any compatibility path remains on purpose, document:

- why it still exists
- what would need to happen to remove it
- what tests protect it today

**Step 4: Commit**

```bash
git commit --allow-empty -m "chore: verify migration cleanup"
```
