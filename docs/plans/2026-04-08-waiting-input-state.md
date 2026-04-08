# Waiting Input State Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the implicit `completed + clarification_request` protocol with an explicit workflow waiting-input state while preserving clarification continuation behavior.

**Architecture:** Keep the existing `clarification` planner node and executor family, but promote user-input pauses into first-class workflow state via `WorkflowRun.pending_interaction` and `RUN_STATUS_WAITING_INPUT`. `ChatService` continues to surface the clarification prompt to clients, but now reads it from structured run state instead of `shared_state`.

**Tech Stack:** Python dataclasses, workflow runtime/agent graph, pytest.

### Task 1: Lock the waiting-input contract with failing tests

**Files:**
- Modify: `tests/test_workflow_models.py`
- Modify: `tests/test_workflow_runtime.py`
- Modify: `tests/test_workflow_continuation.py`
- Modify: `tests/test_workflow_persistence.py`

**Step 1: Write failing tests**

Add coverage for:
- `RUN_STATUS_WAITING_INPUT` and `WorkflowRun.pending_interaction`.
- `GraphExecutionRuntime` pausing on an executor-provided interaction request.
- `AgentGraphRuntime` returning `waiting_input` instead of `completed` for clarification branches.
- `ChatService` sourcing continuation state from structured pending interaction data.
- Persistence restoring pending interaction payloads.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_workflow_models.py tests/test_workflow_runtime.py tests/test_workflow_continuation.py tests/test_workflow_persistence.py -q`
Expected: FAIL because the current implementation still relies on `shared_state["clarification_request"]` and has no waiting-input run state.

### Task 2: Implement explicit waiting-input models and runtime handling

**Files:**
- Modify: `agent_runtime_framework/workflow/state/models.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Modify: `agent_runtime_framework/workflow/state/__init__.py`
- Modify: `agent_runtime_framework/workflow/runtime/execution.py`
- Modify: `agent_runtime_framework/workflow/state/persistence.py`
- Modify: `agent_runtime_framework/workflow/state/graph_state_store.py`

**Step 1: Write minimal implementation**

Add:
- `InteractionRequest`
- `RUN_STATUS_WAITING_INPUT`
- `WorkflowRun.pending_interaction`
- `NodeResult.interaction_request`

Teach runtime/persistence layers to stop, serialize, and restore on pending input.

**Step 2: Run targeted tests**

Run: `pytest tests/test_workflow_models.py tests/test_workflow_runtime.py tests/test_workflow_persistence.py -q`
Expected: PASS for core state/runtime cases, with continuation/API tests still possibly failing.

### Task 3: Migrate clarification flow to the new contract

**Files:**
- Modify: `agent_runtime_framework/workflow/executors/clarification.py`
- Modify: `agent_runtime_framework/workflow/executors/target_resolution.py`
- Modify: `agent_runtime_framework/workflow/nodes/semantic.py`
- Modify: `agent_runtime_framework/workflow/runtime/agent_graph.py`
- Modify: `agent_runtime_framework/api/services/chat_service.py`
- Modify: `agent_runtime_framework/api/bootstrap.py`
- Modify: `agent_runtime_framework/api/state/runtime_state.py`

**Step 1: Write minimal implementation**

Emit interaction requests from clarification-producing executors, thread them through `AgentGraphRuntime`, and let `ChatService` expose pending interaction payloads and continuation routing from structured run state.

**Step 2: Run targeted tests**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_continuation.py -q`
Expected: PASS.

### Task 4: Verify end-to-end regression surface

**Files:**
- Test: `tests/test_workflow_models.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_continuation.py`
- Test: `tests/test_workflow_persistence.py`

**Step 1: Run verification**

Run: `pytest tests/test_workflow_models.py tests/test_workflow_runtime.py tests/test_workflow_continuation.py tests/test_workflow_persistence.py -q`
Expected: PASS.
