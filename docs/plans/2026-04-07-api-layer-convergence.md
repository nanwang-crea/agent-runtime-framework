# API Layer Convergence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Collapse `agent_runtime_framework/api/` into clear entrypoint, route, and service layers while keeping all workflow runtime and graph orchestration in `workflow/`.

**Architecture:** Route modules remain thin HTTP/SSE adapters. `chat_service.py` and `run_service.py` directly coordinate the workflow runtime, payload building, persistence, and replay logic they need through file-local helpers instead of a shared workflow control layer. `runtime_state.py` becomes a state/payload container only, and bootstrap/service assembly becomes shallow wiring.

**Tech Stack:** Python, FastAPI, pytest, existing `workflow/*` runtimes and persistence store.

### Task 1: Lock the target structure with failing tests

**Files:**
- Modify: `tests/test_api_structure.py`
- Modify: `tests/test_workflow_node_registry.py`
- Modify: `tests/test_workflow_continuation.py`
- Test: `tests/test_api_structure.py`
- Test: `tests/test_workflow_node_registry.py`
- Test: `tests/test_workflow_continuation.py`

**Step 1: Write the failing tests**

Add assertions that:
- `agent_runtime_framework/api/services/workflow_service.py` no longer exists.
- `agent_runtime_framework/api/run_lifecycle.py` no longer exists.
- `agent_runtime_framework/api/agent_branch_orchestrator.py` no longer exists.
- `agent_runtime_framework/api/workflow_branch_orchestrator.py` no longer exists.
- `agent_runtime_framework/api/workflow_payload_builder.py` no longer exists.
- `agent_runtime_framework/api/workflow_run_observer.py` no longer exists.
- `agent_runtime_framework/api/pending_run_registry.py` no longer exists.
- `ChatService` exposes direct runtime-building helpers instead of a `workflow` dependency.
- `RunService` exposes direct approve/replay behavior instead of a `workflow` dependency.
- Workflow runtime builder and clarification continuation coverage move to `chat_service.py`.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_structure.py tests/test_workflow_node_registry.py tests/test_workflow_continuation.py -q`
Expected: FAIL because old modules still exist and tests still point at old implementation locations.

**Step 3: Commit**

Do not commit yet. Keep moving after the red phase is confirmed.

### Task 2: Move chat workflow coordination into `chat_service.py`

**Files:**
- Modify: `agent_runtime_framework/api/services/chat_service.py`
- Modify: `agent_runtime_framework/api/runtime_state.py`
- Modify: `agent_runtime_framework/api/services/__init__.py`
- Test: `tests/test_demo_api.py`
- Test: `tests/test_workflow_node_registry.py`
- Test: `tests/test_workflow_continuation.py`

**Step 1: Write/adjust failing tests**

Add or update tests so they cover:
- `ChatService.chat()` ensuring session, building root runtime, and returning workflow payloads.
- `ChatService.stream_chat()` producing SSE events from its own chat payload path.
- `ChatService` hosting graph runtime construction and clarification continuation logic directly.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_demo_api.py tests/test_workflow_node_registry.py tests/test_workflow_continuation.py -q`
Expected: FAIL on new direct-ownership expectations.

**Step 3: Write minimal implementation**

Implement file-local helpers in `chat_service.py` for:
- runtime context lookup
- graph execution runtime creation
- agent runtime creation
- workflow payload assembly
- run observation / history recording
- conversation branch execution
- agent branch execution
- root runtime construction

Update `runtime_state.py` so chat behavior no longer depends on `workflow_service.py`, preferably by delegating to `ChatService` temporarily or removing direct chat methods if no callers remain.

Update `services/__init__.py` to construct `ChatService(runtime_app)` directly.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_demo_api.py tests/test_workflow_node_registry.py tests/test_workflow_continuation.py -q`
Expected: PASS.

### Task 3: Move approve/replay lifecycle into `run_service.py`

**Files:**
- Modify: `agent_runtime_framework/api/services/run_service.py`
- Modify: `agent_runtime_framework/api/services/__init__.py`
- Test: `tests/test_demo_api.py`
- Test: `tests/test_workflow_continuation.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1: Write/adjust failing tests**

Add or update tests so they cover:
- `RunService.approve()` restoring a pending token from in-service storage and resuming the correct runtime kind.
- `RunService.replay()` loading persisted runs first and falling back to replay-by-input when needed.
- Missing token and missing run payloads being owned by `RunService`.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_demo_api.py tests/test_workflow_continuation.py tests/test_workflow_runtime.py -q`
Expected: FAIL on direct `RunService` ownership assertions.

**Step 3: Write minimal implementation**

Implement file-local helpers in `run_service.py` for:
- pending token registration/consumption
- workflow payload rebuilding
- workflow run remembering / recording
- missing token and missing run payloads

Ensure `chat_service.py` registers pending approvals through shared runtime state storage so `run_service.py` can resume them without another orchestration layer.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_demo_api.py tests/test_workflow_continuation.py tests/test_workflow_runtime.py -q`
Expected: PASS.

### Task 4: Remove obsolete API control-layer files

**Files:**
- Delete: `agent_runtime_framework/api/services/workflow_service.py`
- Delete: `agent_runtime_framework/api/run_lifecycle.py`
- Delete: `agent_runtime_framework/api/services/run_lifecycle_service.py`
- Delete: `agent_runtime_framework/api/agent_branch_orchestrator.py`
- Delete: `agent_runtime_framework/api/workflow_branch_orchestrator.py`
- Delete: `agent_runtime_framework/api/workflow_payload_builder.py`
- Delete: `agent_runtime_framework/api/workflow_run_observer.py`
- Delete: `agent_runtime_framework/api/pending_run_registry.py`
- Modify: `agent_runtime_framework/api/bootstrap.py`
- Modify: `agent_runtime_framework/api/app.py`
- Modify: `agent_runtime_framework/api/services/__init__.py`
- Test: `tests/test_api_structure.py`

**Step 1: Write/adjust failing tests**

Expand structure tests to ensure bootstrap only builds state/services and `app.py` no longer imports any deleted control-layer modules.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_structure.py -q`
Expected: FAIL until the files and imports are removed.

**Step 3: Write minimal implementation**

Delete the obsolete files and simplify bootstrap/service assembly accordingly.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_structure.py -q`
Expected: PASS.

### Task 5: Update docs and run focused regression

**Files:**
- Modify: `README.md`
- Modify: `docs/当前Agent设计框架.md`
- Modify: `docs/architecture/final-agent-graph-runtime.md`
- Test: `tests/test_demo_api.py`
- Test: `tests/test_api_structure.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_continuation.py`

**Step 1: Update docs**

Describe the new route -> service -> workflow flow and remove references to deleted API control layers.

**Step 2: Run focused regression**

Run: `pytest tests/test_demo_api.py tests/test_api_structure.py tests/test_workflow_runtime.py tests/test_workflow_continuation.py -q`
Expected: PASS.

**Step 3: Run final grep**

Run: `rg -n "runtime_factory|workflow_service|run_lifecycle|pending_run_registry|workflow_payload_builder|workflow_run_observer|agent_branch_orchestrator|workflow_branch_orchestrator|build_" agent_runtime_framework/api tests`
Expected: only legitimate workflow-layer `build_*` helpers and no references to deleted API control layers.
