# Remove Agent Profile Selection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the API/frontend agent-profile selection layer and always operate with the default workspace agent.

**Architecture:** Delete the profile registry and stop threading `agent_profile`, `active_agent`, and `available_agents` through API context/session payloads. Keep `/api/context` only for workspace switching, and simplify the frontend shell so it no longer renders or manages agent-selection state.

**Tech Stack:** Python dataclasses and FastAPI services on the backend, React + TypeScript in `frontend-shell`, pytest for regression coverage.

### Task 1: Lock the removal behavior with failing tests

**Files:**
- Modify: `tests/test_demo_api.py`
- Modify: `tests/test_api_structure.py`
- Modify: `tests/test_demo_profiles.py`

**Step 1: Write the failing tests**

Add or update tests so they assert:
- `agent_runtime_framework/api/models/agent_profiles.py` no longer exists.
- `/api/context` ignores agent-profile switching and keeps the default behavior while still allowing workspace switching.
- API context/session payload fixtures no longer contain `active_agent` or `available_agents`.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_demo_api.py tests/test_api_structure.py tests/test_demo_profiles.py -q`
Expected: FAIL because the codebase still exposes the profile layer.

### Task 2: Delete backend profile-selection plumbing

**Files:**
- Delete: `agent_runtime_framework/api/models/agent_profiles.py`
- Modify: `agent_runtime_framework/api/models/__init__.py`
- Modify: `agent_runtime_framework/api/bootstrap.py`
- Modify: `agent_runtime_framework/api/state/runtime_state.py`
- Modify: `agent_runtime_framework/api/services/context_service.py`
- Modify: `agent_runtime_framework/api/routes/context_routes.py`
- Modify: `agent_runtime_framework/api/responses/view_payloads.py`
- Modify: `agent_runtime_framework/api/responses/session_responses.py`
- Modify: `agent_runtime_framework/api/responses/error_payloads.py`
- Modify: `agent_runtime_framework/api/responses/error_responses.py`

**Step 1: Write minimal implementation**

Remove profile-specific dataclasses/fields and simplify the context payload to workspace-centric data only.

**Step 2: Run targeted tests**

Run: `pytest tests/test_demo_api.py tests/test_api_structure.py tests/test_demo_profiles.py -q`
Expected: PASS.

### Task 3: Simplify frontend state and API client

**Files:**
- Modify: `frontend-shell/src/api.ts`
- Modify: `frontend-shell/src/types.ts`
- Modify: `frontend-shell/src/App.tsx`

**Step 1: Write minimal implementation**

Remove agent-selection types, request parameters, and UI affordances. Keep workspace switching and default shell behavior intact.

**Step 2: Run any relevant tests**

Run: `pytest tests/test_demo_api.py tests/test_api_structure.py -q`
Expected: PASS.

### Task 4: Verify the related regression surface

**Files:**
- Test: `tests/test_demo_api.py`
- Test: `tests/test_api_structure.py`
- Test: `tests/test_workflow_continuation.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1: Run verification**

Run: `pytest tests/test_demo_api.py tests/test_api_structure.py tests/test_workflow_continuation.py tests/test_workflow_runtime.py -q`
Expected: PASS.
