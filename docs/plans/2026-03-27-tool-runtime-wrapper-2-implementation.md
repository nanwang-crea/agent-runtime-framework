# Tool Runtime Wrapper 2.0 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make tool execution more stable by adding validation, tool-name repair, argument alias repair, invalid-tool fallback, and a standard structured error shape across the Codex execution path.

**Architecture:** Keep the new behavior centered in the existing `tools/executor.py` and Codex tool-dispatch path. Avoid a large framework rewrite. Use small extensions to `ToolSpec`, `ToolResult`, and `ToolRegistry` so the runtime can normalize calls before execution and return consistent machine-readable failures after execution.

**Tech Stack:** Python dataclasses, existing Codex runtime, pytest

## Status

Implementation is complete.

Delivered outcomes:
- Added `required_arguments` and `argument_aliases` support on `ToolSpec`
- Added structured `metadata` on `ToolResult`
- Added case-insensitive lookup and suggestions on `ToolRegistry`
- Added executor-side argument alias repair
- Added executor-side required argument and input type validation
- Added standardized `TOOL_VALIDATION_ERROR` and `TOOL_EXECUTION_ERROR` payloads
- Added Codex loop repair for case-mismatched tool names
- Added Codex loop structured `TOOL_NOT_FOUND` fallback

Verification:
- Ran `pytest tests/test_tool_registry.py tests/test_codex_agent.py tests/test_assistant_runtime.py tests/test_demo_app.py -v`
- Result: `161 passed`

### Task 1: Add failing tests for tool runtime wrapper behavior

Status: `completed`

**Files:**
- Modify: `tests/test_tool_registry.py`
- Modify: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests for:
- case-insensitive tool-name repair
- argument alias repair
- input type validation
- missing required argument failure
- standardized structured error payload
- Codex loop fallback when tool name is invalid

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_tool_registry.py tests/test_codex_agent.py -k "tool_runtime or invalid_tool or validation or repair" -v`
Expected: FAIL because the current runtime does not implement these behaviors.

**Step 3: Write minimal implementation**

Patch the tool runtime and Codex dispatch path until the tests pass.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_tool_registry.py tests/test_codex_agent.py -k "tool_runtime or invalid_tool or validation or repair" -v`
Expected: PASS

### Task 2: Implement tool runtime wrapper 2.0

Status: `completed`

**Files:**
- Modify: `agent_runtime_framework/core/specs.py`
- Modify: `agent_runtime_framework/tools/models.py`
- Modify: `agent_runtime_framework/tools/registry.py`
- Modify: `agent_runtime_framework/tools/executor.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`

**Step 1: Write the failing test**

Add or extend tests so the runtime must:
- normalize tool names before `require()`
- normalize argument aliases before executor invocation
- reject invalid argument types with structured metadata
- reject unknown tools with structured metadata and available candidates

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_tool_registry.py tests/test_codex_agent.py -k "invalid_tool or validation or repair" -v`
Expected: FAIL on missing normalization and error metadata.

**Step 3: Write minimal implementation**

Implement:
- optional argument alias metadata on `ToolSpec`
- tool lookup helpers on `ToolRegistry`
- executor-side normalization and validation
- standardized `ToolResult.metadata["error"]`
- Codex loop dispatch fallback for unknown tool names

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_tool_registry.py tests/test_codex_agent.py -k "invalid_tool or validation or repair" -v`
Expected: PASS

### Task 3: Full verification and docs sync

Status: `completed`

**Files:**
- Modify: `docs/2026-03-26-Agent升级校验与演进建议.md`
- Modify: `docs/plans/2026-03-27-tool-runtime-wrapper-2-implementation.md`

**Step 1: Run targeted suites**

Run: `pytest tests/test_tool_registry.py tests/test_codex_agent.py tests/test_assistant_runtime.py tests/test_demo_app.py -v`
Observed: PASS (`161 passed`)

**Step 2: Update docs**

Document:
- what Tool Runtime Wrapper 2.0 now covers
- what remains after this round
- test evidence
