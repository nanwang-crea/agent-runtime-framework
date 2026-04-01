# Model-First Graph Expansion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make workflow graph compilation the primary orchestration path for all non-chat requests, including codex execution, verification, repair, and approval insertion.

**Architecture:** Route all non-conversation requests through workflow runtime. Let graph compilation decide node topology and executor kind, then execute native nodes and codex-backed nodes through the same runtime. In configured model-only environments, reject invalid model graphs by falling back only when the environment does not require strict model-only behavior.

**Tech Stack:** Python dataclasses, workflow runtime, demo app routing, pytest.

### Task 1: Add failing graph-builder coverage

**Files:**
- Modify: `tests/test_workflow_graph_builder.py`
- Modify: `tests/test_demo_app.py`
- Modify: `tests/test_workflow_runtime.py`

**Step 1:** Add tests for codex node insertion, workflow-first routing for non-chat requests, and model-only enforcement.

**Step 2:** Run targeted pytest commands and confirm they fail for the expected missing behavior.

### Task 2: Expand workflow graph compilation

**Files:**
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Modify: `agent_runtime_framework/workflow/models.py`
- Modify: `agent_runtime_framework/workflow/runtime.py`

**Step 1:** Let compiled graphs carry executor metadata and post-action node insertion such as verification, repair, and approval.

**Step 2:** Support strict model-only mode for configured environments while preserving fallback behavior elsewhere.

### Task 3: Route demo app through workflow runtime

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/workflow/codex_subtask.py`

**Step 1:** Route all non-chat requests through workflow runtime.

**Step 2:** Register codex execution as a graph executor rather than a separate app-level branch.

### Task 4: Verify behavior

**Files:**
- Test: `tests/test_workflow_graph_builder.py`
- Test: `tests/test_demo_app.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1:** Run focused pytest suites.

**Step 2:** If green, summarize behavior changes and remaining tradeoffs.
