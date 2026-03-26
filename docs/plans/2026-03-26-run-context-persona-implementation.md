# Run Context And Persona Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a shared run-context builder and wire runtime personas through the Codex runtime so planner, evaluator, classifier, and conversation use the same environment snapshot and persona-aware tool visibility.

**Architecture:** Keep the new logic small and local to the existing Codex path. Reuse the existing `ApplicationContext`, `AssistantSession`, `CodexTask`, and tool registry instead of adding a new framework layer. Persona controls prompt preamble, tool visibility, write permission posture, and step budget metadata; run context is a structured snapshot rendered into prompt-facing text where needed.

**Tech Stack:** Python dataclasses, existing Codex runtime, pytest

### Task 1: Lock down run-context and persona behavior with tests

**Files:**
- Modify: `tests/test_codex_agent.py`
- Modify: `tests/test_assistant_runtime.py`

**Step 1: Write failing tests**

Add tests for:
- `build_run_context()` includes workspace, permissions, instructions, memory, plan state, and persona data
- `available_tool_names()` filters write tools for read-only personas
- Codex task creation stores the resolved runtime persona
- conversation prompt building can include the shared run context block

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py tests/test_assistant_runtime.py -k "run_context or persona or conversation" -v`
Expected: FAIL on missing or incomplete persona/run-context behavior.

**Step 3: Write minimal implementation**

Patch the runtime and session plumbing until these tests pass.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py tests/test_assistant_runtime.py -k "run_context or persona or conversation" -v`
Expected: PASS

### Task 2: Finish Codex runtime persona plumbing

**Files:**
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Modify: `agent_runtime_framework/agents/codex/models.py`
- Modify: `agent_runtime_framework/agents/codex/personas.py`
- Modify: `agent_runtime_framework/agents/codex/planner.py`
- Modify: `agent_runtime_framework/agents/codex/evaluator.py`
- Modify: `agent_runtime_framework/agents/codex/task_plans.py`
- Modify: `agent_runtime_framework/agents/codex/profiles.py`

**Step 1: Write the failing test**

Add tests that assert:
- task build path assigns a non-empty `runtime_persona`
- planner and plan builder only see persona-allowed tools
- evaluator model prompt path receives persona-aware context without runtime errors

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "persona or planner or evaluator" -v`
Expected: FAIL because persona is not fully assigned or evaluator still has incomplete plumbing.

**Step 3: Write minimal implementation**

Implement:
- task persona assignment in `_build_task()`
- persona-aware tool filtering for planning and plan construction
- evaluator signature and prompt assembly fixes
- persona metadata propagation into runtime events

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "persona or planner or evaluator" -v`
Expected: PASS

### Task 3: Finish shared run-context builder

**Files:**
- Modify: `agent_runtime_framework/agents/codex/run_context.py`
- Modify: `agent_runtime_framework/agents/codex/prompting.py`
- Modify: `agent_runtime_framework/assistant/conversation.py`
- Modify: `agent_runtime_framework/assistant/session.py`
- Modify: `agent_runtime_framework/demo/app.py`

**Step 1: Write the failing test**

Add tests that assert:
- current user message and recent turns are handled consistently
- `loaded_instructions` can come from config/services/index memory
- session can expose active persona for follow-up work
- demo context payload includes active persona when available

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py tests/test_assistant_runtime.py tests/test_demo_app.py -k "run_context or persona or context_payload" -v`
Expected: FAIL where run-context/session persona data is not surfaced yet.

**Step 3: Write minimal implementation**

Implement:
- active persona tracking on session/context
- shared run-context block use in conversation/system prompts
- demo/context payload exposure for active persona

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py tests/test_assistant_runtime.py tests/test_demo_app.py -k "run_context or persona or context_payload" -v`
Expected: PASS

### Task 4: Verify the full Codex path

**Files:**
- Modify: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add a high-level integration test covering:
- repository explanation request resolves to `explore`
- change request resolves to `build`
- resulting prompt-facing answer path still completes successfully

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "repository_explainer_profile or change_and_verify_profile or run_context" -v`
Expected: FAIL if persona assignment or prompt/runtime integration is still inconsistent.

**Step 3: Write minimal implementation**

Patch any missing glue while keeping the design local and simple.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "repository_explainer_profile or change_and_verify_profile or run_context" -v`
Expected: PASS

### Task 5: Full verification

**Files:**
- Test: `tests/test_codex_agent.py`
- Test: `tests/test_assistant_runtime.py`
- Test: `tests/test_demo_app.py`

**Step 1: Run targeted suites**

Run: `pytest tests/test_codex_agent.py tests/test_assistant_runtime.py tests/test_demo_app.py -v`
Expected: PASS

**Step 2: Commit**

```bash
git add agent_runtime_framework/agents/codex/personas.py \
  agent_runtime_framework/agents/codex/run_context.py \
  agent_runtime_framework/agents/codex/models.py \
  agent_runtime_framework/agents/codex/loop.py \
  agent_runtime_framework/agents/codex/planner.py \
  agent_runtime_framework/agents/codex/evaluator.py \
  agent_runtime_framework/agents/codex/task_plans.py \
  agent_runtime_framework/agents/codex/profiles.py \
  agent_runtime_framework/agents/codex/prompting.py \
  agent_runtime_framework/assistant/conversation.py \
  agent_runtime_framework/assistant/session.py \
  agent_runtime_framework/demo/app.py \
  tests/test_codex_agent.py \
  tests/test_assistant_runtime.py \
  tests/test_demo_app.py \
  docs/plans/2026-03-26-run-context-persona-implementation.md
git commit -m "feat: add run context builder and runtime personas"
```
