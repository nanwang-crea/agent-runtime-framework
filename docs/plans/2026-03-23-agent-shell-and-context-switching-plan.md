# Agent Shell And Context Switching Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a shell-level agent/workspace context model so the same session can switch between Codex-style execution and Q&A mode, while continuing to migrate desktop create/edit behaviors into Codex tools and strengthening LLM next-action planning.

**Architecture:** The demo app becomes a shell host that owns one session and one mutable execution context. Agent switching changes how the shell routes messages (`codex` vs `qa_only`), while workspace switching rebinds the current application context to a different root without discarding the conversation. Codex tools continue absorbing desktop behaviors so the shell talks to an action-centric runtime rather than a capability-centric desktop application.

**Tech Stack:** Python 3.11+, pytest, React, TypeScript, Vite, existing demo HTTP API

### Task 1: Add shell context payloads and switching

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/demo/server.py`
- Modify: `frontend-shell/src/api.ts`
- Modify: `frontend-shell/src/types.ts`
- Test: `tests/test_demo_app.py`
- Test: `tests/test_frontend_shell_scaffold.py`

**Step 1: Write the failing test**

Add tests for:

- switching agent profile within the same session
- switching workspace within the same session
- frontend shell referencing `/api/context`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_demo_app.py -k "switch_agent_profile or switch_workspace" -v`
Expected: FAIL because `switch_context()` and `/api/context` do not exist yet.

**Step 3: Write minimal implementation**

Add:

- `DemoAssistantApp.context_payload()`
- `DemoAssistantApp.switch_context()`
- `/api/context` server endpoint
- frontend context types and API helper

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_demo_app.py -k "switch_agent_profile or switch_workspace" -v`
Expected: PASS

### Task 2: Finish create/edit migration into Codex tools

**Files:**
- Modify: `agent_runtime_framework/agents/codex/tools.py`
- Modify: `agent_runtime_framework/agents/codex/planner.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests for:

- creating a file through default codex tooling
- editing a file through default codex tooling
- approval flow for both write actions

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "creates_workspace_file or edits_workspace_file" -v`
Expected: FAIL because planner does not emit these actions yet.

**Step 3: Write minimal implementation**

Add:

- `create_workspace_path`
- `edit_workspace_text`
- planner rules mapping user input to `create_path` and `edit_text`
- artifact generation for these write actions

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "creates_workspace_file or edits_workspace_file" -v`
Expected: PASS

### Task 3: Tighten LLM next-action planner

**Files:**
- Modify: `agent_runtime_framework/agents/codex/planner.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add a test that confirms the planner:

- uses an LLM when configured
- sees tool context
- uses recent observations for the next step

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "llm_next_action_planner" -v`
Expected: FAIL because the planner is still purely heuristic.

**Step 3: Write minimal implementation**

Use `resolve_model_runtime()` and `parse_structured_output()` to make the planner LLM-first. Include:

- tool descriptions
- permission levels
- risk hints
- workspace root
- recent action observations

Rules remain as fallback.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "llm_next_action_planner" -v`
Expected: PASS

### Task 4: Wire the frontend shell to the new context model

**Files:**
- Modify: `frontend-shell/src/App.tsx`
- Test: `tests/test_frontend_shell_scaffold.py`

**Step 1: Write the failing test**

Require the shell to mention agent/workspace switching and use the new context API.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_frontend_shell_scaffold.py -k "agent_and_workspace_switching" -v`
Expected: FAIL until the shell references `/api/context`.

**Step 3: Write minimal implementation**

Add:

- agent selector
- workspace selector
- context state hydration from `/api/session`
- context switch requests through `/api/context`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_frontend_shell_scaffold.py -k "agent_and_workspace_switching" -v`
Expected: PASS

## Verification

Run:

- `pytest tests/test_codex_agent.py -q`
- `pytest tests/test_demo_app.py -q`
- `pytest tests/test_frontend_shell_scaffold.py -q`
- `pytest tests/test_assistant_runtime.py tests/test_tool_registry.py tests/test_models.py -q`
- `cd frontend-shell && npm run build`

## Result

After this slice:

- the demo acts as an `Agent Shell`
- Codex tools cover create/edit/move/delete/read/list/summarize/verify
- the LLM planner is no longer only rule-based
- `desktop_content_application` is further reduced to compatibility value rather than product value
