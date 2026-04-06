# Workspace Subtask Removal Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `workspace_subtask` with graph-native filesystem and text-edit nodes, then remove the bridge from the main runtime.

**Architecture:** Keep the current graph-first runtime, but stop sending modify requests through a generic compatibility bridge. Instead, add a small set of explicit write-oriented nodes that map onto the already-existing fine-grained workspace tools: filesystem operations first, then text editing operations. `workspace_subtask` should remain only until those nodes are proven end-to-end, then be deleted along with its runner, factory wiring, and fallback routing.

**Tech Stack:** Python 3.10+, dataclasses, pytest, local file-backed persistence, existing workspace tool registry

### Task 1: Freeze the replacement node taxonomy

Status: completed

**Files:**
- Modify: `README.md`
- Modify: `docs/当前Agent设计框架.md`
- Create: `docs/architecture/workspace-write-nodes.md`
- Test: `tests/test_public_surface.py`

**Step 1: Write the node inventory into the new architecture note**

Document these first-class graph-native node types:

- `create_path`
- `move_path`
- `delete_path`
- `apply_patch`
- `write_file`
- `append_text`
- `verification`

State that:

- node names express workflow-stage intent
- tools remain lower-level execution primitives
- `workspace_subtask` is being removed, not expanded

**Step 2: Run the current public-surface test**

Run: `pytest tests/test_public_surface.py -v`
Expected: PASS

**Step 3: Update the current docs**

Make the docs say:

- filesystem changes are moving to graph-native nodes
- `workspace_subtask` is temporary and scheduled for removal
- tools stay fine-grained; nodes do not mirror tools one-to-one

**Step 4: Add a narrow docs-facing assertion only if stable**

Freeze only wording that is genuinely part of the public architecture surface.

**Step 5: Commit**

```bash
git add README.md docs/当前Agent设计框架.md docs/architecture/workspace-write-nodes.md tests/test_public_surface.py
git commit -m "docs: define graph-native write node taxonomy"
```

### Task 2: Add graph-native filesystem operation nodes

Status: completed

**Files:**
- Modify: `agent_runtime_framework/workflow/models.py`
- Modify: `agent_runtime_framework/workflow/subgraph_planner.py`
- Modify: `agent_runtime_framework/demo/runtime_factory.py`
- Create: `agent_runtime_framework/workflow/filesystem_node_executors.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing tests**

Add tests that prove:

- planner emits `create_path` / `move_path` / `delete_path` instead of `workspace_subtask` for filesystem requests
- runtime factory registers executors for those node types
- each node delegates to the correct existing workspace tool

**Step 2: Run the targeted tests to confirm failure**

Run: `pytest tests/test_workflow_runtime.py tests/test_demo_app.py -v`
Expected: FAIL because the node types/executors do not exist yet.

**Step 3: Implement minimal executors**

Map each node to the existing tool layer:

- `create_path` -> `create_workspace_path`
- `move_path` -> `move_workspace_path`
- `delete_path` -> `delete_workspace_path`

Keep the node output shape aligned with other workflow executors.

**Step 4: Update planner rules**

Teach deterministic planning to emit filesystem nodes for:

- create file or directory
- rename or move
- delete

Do not touch text-edit flows yet.

**Step 5: Run the targeted tests**

Run: `pytest tests/test_workflow_runtime.py tests/test_demo_app.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add agent_runtime_framework/workflow/models.py agent_runtime_framework/workflow/subgraph_planner.py agent_runtime_framework/demo/runtime_factory.py agent_runtime_framework/workflow/filesystem_node_executors.py tests/test_workflow_runtime.py tests/test_demo_app.py
git commit -m "feat: add graph-native filesystem nodes"
```

### Task 3: Add graph-native text-edit nodes

Status: in_progress

**Files:**
- Modify: `agent_runtime_framework/workflow/subgraph_planner.py`
- Modify: `agent_runtime_framework/demo/runtime_factory.py`
- Modify: `agent_runtime_framework/workflow/filesystem_node_executors.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_decomposition.py`

**Step 1: Write the failing tests**

Add tests that prove:

- `change_and_verify` requests emit explicit write nodes instead of `workspace_subtask`
- `apply_patch`, `write_file`, and `append_text` each map to the correct tool
- plain file reads still stay on the read chain

**Step 2: Run the targeted tests to confirm failure**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_decomposition.py -v`
Expected: FAIL because modify flows still fall back to `workspace_subtask`.

**Step 3: Implement minimal text-edit executors**

Map:

- `apply_patch` -> `apply_text_patch`
- `write_file` -> `edit_workspace_text`
- `append_text` -> `append_workspace_text`

**Step 4: Update planner emission**

Choose explicit nodes for the most common modify intents. Keep it deterministic and simple:

- full rewrite -> `write_file`
- targeted replacement -> `apply_patch`
- append -> `append_text`

If the request is still too underspecified, prefer clarification over `workspace_subtask`.

**Step 5: Run the targeted tests**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_decomposition.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add agent_runtime_framework/workflow/subgraph_planner.py agent_runtime_framework/demo/runtime_factory.py agent_runtime_framework/workflow/filesystem_node_executors.py tests/test_workflow_runtime.py tests/test_workflow_decomposition.py
git commit -m "feat: add graph-native text edit nodes"
```

### Task 4: Move modify requests fully off `workspace_subtask`

**Files:**
- Modify: `agent_runtime_framework/workflow/subgraph_planner.py`
- Modify: `agent_runtime_framework/workflow/workspace_subtask.py`
- Test: `tests/test_workflow_decomposition.py`
- Test: `tests/test_workflow_codex_subtask.py`

**Step 1: Write the failing tests**

Add tests that assert:

- filesystem and text-edit requests no longer emit `workspace_subtask`
- `workspace_subtask` is only reachable for explicitly unsupported categories
- fallback metadata remains available for the few categories still using it

**Step 2: Run the targeted tests to confirm failure**

Run: `pytest tests/test_workflow_decomposition.py tests/test_workflow_codex_subtask.py -v`
Expected: FAIL because modify intents still route through `workspace_subtask`.

**Step 3: Narrow the fallback boundary**

Remove modify-intent routing to `workspace_subtask`. Keep it only for truly unsupported classes of work.

**Step 4: Run the targeted tests**

Run: `pytest tests/test_workflow_decomposition.py tests/test_workflow_codex_subtask.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/subgraph_planner.py agent_runtime_framework/workflow/workspace_subtask.py tests/test_workflow_decomposition.py tests/test_workflow_codex_subtask.py
git commit -m "refactor: remove modify flows from workspace_subtask"
```

### Task 5: Remove `workspace_subtask` runtime wiring

**Files:**
- Delete or modify: `agent_runtime_framework/workflow/workspace_subtask.py`
- Delete or modify: `agent_runtime_framework/demo/compat_subtask_runner.py`
- Modify: `agent_runtime_framework/demo/runtime_factory.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Test: `tests/test_public_surface.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing tests**

Add tests that assert:

- runtime factory no longer registers `workspace_subtask`
- public workflow surface no longer exports `WorkspaceSubtaskExecutor`
- demo app no longer carries `_compat_subtask_runner`

**Step 2: Run the targeted tests to confirm failure**

Run: `pytest tests/test_public_surface.py tests/test_demo_app.py -v`
Expected: FAIL because the bridge is still wired in.

**Step 3: Delete the bridge**

Remove:

- `CompatSubtaskRunner`
- `WorkspaceSubtaskExecutor`
- runtime factory registration
- related public exports

**Step 4: Run the targeted tests**

Run: `pytest tests/test_public_surface.py tests/test_demo_app.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/demo/runtime_factory.py agent_runtime_framework/workflow/__init__.py tests/test_public_surface.py tests/test_demo_app.py
git rm agent_runtime_framework/demo/compat_subtask_runner.py agent_runtime_framework/workflow/workspace_subtask.py
git commit -m "refactor: remove workspace_subtask bridge"
```

### Task 6: Add end-to-end coverage for graph-native write flows

**Files:**
- Modify: `tests/test_workflow_end_to_end.py`
- Modify: `tests/test_demo_app.py`
- Modify: `tests/test_workflow_runtime.py`

**Step 1: Add missing regression tests**

Cover:

- create file request
- rename/move request
- delete request with approval
- full rewrite request
- apply patch request
- append request
- verification after modify

**Step 2: Run the focused suite**

Run: `pytest tests/test_workflow_end_to_end.py tests/test_demo_app.py tests/test_workflow_runtime.py -v`
Expected: PASS

**Step 3: Run the full suite**

Run: `pytest -q`
Expected: PASS

**Step 4: Commit**

```bash
git add tests/test_workflow_end_to_end.py tests/test_demo_app.py tests/test_workflow_runtime.py
git commit -m "test: cover graph-native write flows"
```

### Task 7: Final cleanup and docs closeout

**Files:**
- Modify: `README.md`
- Modify: `docs/当前Agent设计框架.md`
- Modify: `docs/architecture/final-agent-graph-runtime.md`

**Step 1: Remove bridge-era wording**

Delete references implying `workspace_subtask` still exists as a runtime boundary.

**Step 2: Describe the new write path**

Document that:

- filesystem and edit requests are now graph-native
- tools are fine-grained primitives
- nodes represent workflow-stage semantics

**Step 3: Run final verification**

Run: `pytest -q`
Expected: PASS

**Step 4: Commit**

```bash
git add README.md docs/当前Agent设计框架.md docs/architecture/final-agent-graph-runtime.md
git commit -m "docs: describe graph-native write architecture"
```
