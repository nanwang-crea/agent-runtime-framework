# Module Layout Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Clean up a small set of internal module names and locations so the repository better reflects the workflow-first architecture without breaking existing imports.

**Architecture:** Move implementation ownership to clearer module paths, then keep the old modules as compatibility shims. This keeps the public and test-facing import surface stable while letting internal code adopt the cleaner layout immediately.

**Tech Stack:** Python, package-level re-exports, pytest.

### Task 1: Rename workflow planning module away from versioned filename

**Files:**
- Create: `agent_runtime_framework/workflow/subgraph_planner.py`
- Modify: `agent_runtime_framework/workflow/planner_v2.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Modify: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1:** Copy the implementation from `planner_v2.py` into `subgraph_planner.py`.

**Step 2:** Replace `planner_v2.py` with a compatibility re-export shim.

**Step 3:** Update internal imports to prefer `subgraph_planner.py`.

**Step 4:** Add or update tests to prove the new path exports the planner API and the old path still works.

### Task 2: Rename root workflow router module to match its actual responsibility

**Files:**
- Create: `agent_runtime_framework/workflow/routing_runtime.py`
- Modify: `agent_runtime_framework/workflow/root_graph_runtime.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/demo/agent_branch_orchestrator.py`
- Modify: `agent_runtime_framework/demo/runtime_factory.py`
- Test: `tests/test_demo_app.py`

**Step 1:** Move `RootGraphRuntime` implementation into `routing_runtime.py`.

**Step 2:** Keep `root_graph_runtime.py` as a shim re-exporting the typed payload contracts and runtime class.

**Step 3:** Update internal imports to prefer `routing_runtime.py`.

**Step 4:** Add or update tests to cover the new module path.

### Task 3: Rename demo workflow orchestration modules to remove migration-era naming

**Files:**
- Create: `agent_runtime_framework/demo/workflow_branch_orchestrator.py`
- Create: `agent_runtime_framework/demo/run_lifecycle.py`
- Modify: `agent_runtime_framework/demo/compat_workflow_orchestrator.py`
- Modify: `agent_runtime_framework/demo/run_lifecycle_service.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/demo/runtime_factory.py`
- Test: `tests/test_demo_app.py`

**Step 1:** Move implementation into the new names.

**Step 2:** Keep old files as compatibility shims.

**Step 3:** Update internal imports to prefer the new module names.

**Step 4:** Add or update tests to prove the new paths are importable.

### Task 4: Verification

**Files:**
- Test: `tests/test_demo_app.py`
- Test: `tests/test_workflow_graph_builder.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1:** Run focused pytest slices covering the renamed modules.

**Step 2:** Confirm imports and workflow behavior still pass.
