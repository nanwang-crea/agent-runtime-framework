# Workspace Backend Rewrite Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the sprawling `agent_runtime_framework/agents/codex` implementation with a focused workspace backend, while keeping a thin compatibility surface for existing imports.

**Architecture:** Create a new `agent_runtime_framework/agents/workspace_backend/` package that owns loop, planner, evaluator, runtime, models, and tool composition. Reorganize tools into `base/common/file_tools/shell_tools/registry` modules so file and shell capabilities follow a consistent internal abstraction. Rewrite the public `codex` modules into compatibility shims that re-export the new workspace backend APIs instead of preserving the old deep dependency graph.

**Tech Stack:** Python 3.12, dataclasses, existing `ToolSpec`/`ToolRegistry`, local resource repository, sandbox helpers, markdown index memory.

### Task 1: Add workspace backend package

**Files:**
- Create: `agent_runtime_framework/agents/workspace_backend/__init__.py`
- Create: `agent_runtime_framework/agents/workspace_backend/models.py`
- Create: `agent_runtime_framework/agents/workspace_backend/runtime.py`
- Create: `agent_runtime_framework/agents/workspace_backend/evaluator.py`
- Create: `agent_runtime_framework/agents/workspace_backend/planner.py`
- Create: `agent_runtime_framework/agents/workspace_backend/loop.py`

**Step 1: Write the failing test**

Use existing compatibility tests first; no new test required before scaffolding because public imports already fail once shims are redirected.

**Step 2: Run test to verify current baseline**

Run: `pytest tests/test_demo_app.py::test_demo_assistant_app_returns_session_and_plan_history -q`
Expected: PASS on baseline before refactor.

**Step 3: Write minimal implementation**

Add the new package with compact dataclasses and a loop that supports:
- session-aware task construction
- simple default planning
- tool execution via `execute_tool_call`
- clarification persistence via index memory
- final result packaging compatible with the demo app

**Step 4: Run targeted tests**

Run: `pytest tests/test_demo_app.py::test_demo_assistant_app_returns_session_and_plan_history -q`
Expected: PASS after imports are rewired.

### Task 2: Rebuild tool system around workspace backend

**Files:**
- Create: `agent_runtime_framework/agents/workspace_backend/tools/__init__.py`
- Create: `agent_runtime_framework/agents/workspace_backend/tools/base.py`
- Create: `agent_runtime_framework/agents/workspace_backend/tools/common.py`
- Create: `agent_runtime_framework/agents/workspace_backend/tools/file_tools.py`
- Create: `agent_runtime_framework/agents/workspace_backend/tools/shell_tools.py`
- Create: `agent_runtime_framework/agents/workspace_backend/tools/registry.py`

**Step 1: Write the failing test**

Reuse current demo app and planner tests that depend on `build_default_codex_tools` and file resolution behavior.

**Step 2: Run test to verify expected tool behavior coverage**

Run: `pytest tests/test_demo_app.py::test_demo_planner_uses_semantic_directory_detection_before_default_planner -q`
Expected: PASS on baseline.

**Step 3: Write minimal implementation**

Implement a single internal tool definition abstraction plus grouped file/shell tool builders. Preserve public tool names (`resolve_workspace_target`, `read_workspace_text`, `inspect_workspace_path`, `run_shell_command`, etc.) and keep path safety, memory focus updates, and structured outputs.

**Step 4: Run targeted tests**

Run: `pytest tests/test_demo_app.py::test_demo_planner_uses_semantic_directory_detection_before_default_planner -q`
Expected: PASS after tool builder swap.

### Task 3: Replace codex exports with compatibility shims

**Files:**
- Modify: `agent_runtime_framework/agents/codex/__init__.py`
- Modify: `agent_runtime_framework/agents/codex/models.py`
- Modify: `agent_runtime_framework/agents/codex/runtime.py`
- Modify: `agent_runtime_framework/agents/codex/evaluator.py`
- Modify: `agent_runtime_framework/agents/codex/planner.py`
- Modify: `agent_runtime_framework/agents/codex/tools.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Modify: `agent_runtime_framework/agents/workspace_backend.py`

**Step 1: Write the failing test**

Reuse import-driven tests under `tests/test_workflow_codex_subtask.py` and `tests/test_demo_app.py`.

**Step 2: Run test to confirm compatibility contract**

Run: `pytest tests/test_workflow_codex_subtask.py -q`
Expected: PASS on baseline.

**Step 3: Write minimal implementation**

Replace old module contents with re-exports from `workspace_backend`, keeping the same class and function names used by tests and demo code.

**Step 4: Run targeted tests**

Run: `pytest tests/test_workflow_codex_subtask.py -q`
Expected: PASS after shim rewrite.

### Task 4: Validate end-to-end demo behavior

**Files:**
- Modify only if verification exposes missing compatibility edges.

**Step 1: Run focused demo tests**

Run: `pytest tests/test_demo_app.py tests/test_workflow_codex_subtask.py tests/test_entrypoints.py tests/test_subagent_runtime.py -q`
Expected: PASS.

**Step 2: Fix only refactor regressions**

Keep changes local to workspace backend or compatibility shims; do not repair unrelated failures.

**Step 3: Run a broader smoke test**

Run: `pytest -q`
Expected: either PASS or only unrelated pre-existing failures.
