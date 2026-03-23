# Dual-Layer Agent Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the framework into a reusable kernel layer and a Codex-style action-centric agent layer without breaking the current capability-centric assistant runtime.

**Architecture:** Keep the existing `assistant` runtime as a compatibility/profile layer, not the long-term core execution model. Introduce a new `agents/codex` package built around `Task`, `Action`, `ActionResult`, approval checkpoints, artifact recording, and verification-oriented control flow so the framework can support both generic desktop assistants and strong-execution Codex-style agents.

**Tech Stack:** Python 3.11+, dataclasses, existing graph/session/policy/artifact infrastructure, pytest

## Background

The current runtime centers on `CapabilityRegistry` and `AgentLoop`, which is appropriate for a reusable assistant platform but too coarse for a Codex-style agent. Codex-like products are driven by incremental task progress: read files, run commands, apply edits, checkpoint risky operations, verify results, then either continue or finish. That means the framework needs a lower-level execution abstraction than `capability`.

The required layering is:

1. `Kernel`
   Common runtime building blocks: graph, policy, models, tools, artifact store, approval primitives, checkpointing, memory, MCP lifecycle.
2. `Profiles`
   Concrete ways to assemble the kernel for a product or scenario, such as the current desktop assistant.
3. `Agents`
   Product-grade control loops with distinct operating models. The first new one is a Codex-style action-centric agent.

## Non-Goals For This Refactor Slice

- Do not remove or rewrite the current `assistant` runtime.
- Do not migrate every desktop capability into actions in one step.
- Do not redesign the demo UI in this slice.
- Do not introduce multi-agent orchestration yet.

## Target Module Shape

- Add `agent_runtime_framework/agents/__init__.py`
- Add `agent_runtime_framework/agents/codex/__init__.py`
- Add `agent_runtime_framework/agents/codex/models.py`
- Add `agent_runtime_framework/agents/codex/loop.py`
- Add `tests/test_codex_agent.py`
- Update package exports in `agent_runtime_framework/__init__.py`

## Action-Centric Model

The Codex-style runtime should revolve around these concepts:

- `CodexTask`
  Holds the user goal, ordered actions, status, task-level artifact ids, and optional verification result.
- `CodexAction`
  The minimal execution unit. Examples: `respond`, `read_resource`, `run_command`, `apply_patch`, `call_tool`, `request_approval`, `run_verification`, `finish`.
- `CodexActionResult`
  Structured action output including final output text, artifact ids, optional inline artifact payloads, and approval requirements.
- `VerificationResult`
  Records whether the loop has enough evidence to finish.

The first implementation only needs enough fields to support a stable minimal loop and tests. Future action kinds can be added without changing the control flow model.

## Loop Model

The Codex loop should be:

`user input -> task build -> execute next action -> observe result -> store artifacts -> continue/finish`

Important control-flow rules:

- Planning may come from an injected callback, but the runtime owns execution state.
- Risky actions should be able to pause for approval.
- Action outputs should be normalized before loop decisions are made.
- Artifact persistence must happen in the loop, not inside arbitrary agent prompts.
- The loop must work even when no LLM planner is configured.

## Compatibility Strategy

Keep the existing `assistant` package unchanged for now. Treat it as a higher-level profile runtime that can continue to power the current desktop assistant and demo. The new Codex runtime should exist in parallel until it is mature enough to absorb selected desktop operations as first-class actions.

## Migration Path

### Task 1: Add the new Codex models

**Files:**
- Create: `agent_runtime_framework/agents/__init__.py`
- Create: `agent_runtime_framework/agents/codex/__init__.py`
- Create: `agent_runtime_framework/agents/codex/models.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests that construct:

- a `CodexTask` from a list of actions
- a `VerificationResult`
- a `CodexActionResult` with inline artifact payloads

Verify defaults, status fields, and task-level artifact tracking.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k models -v`
Expected: FAIL with import errors because the new package does not exist yet.

**Step 3: Write minimal implementation**

Add dataclasses for:

- `CodexAction`
- `CodexTask`
- `CodexActionResult`
- `VerificationResult`

Use simple, explicit fields. Avoid enums unless they remove real ambiguity.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k models -v`
Expected: PASS

### Task 2: Add the minimal Codex loop

**Files:**
- Create: `agent_runtime_framework/agents/codex/loop.py`
- Modify: `agent_runtime_framework/agents/codex/__init__.py`
- Modify: `agent_runtime_framework/__init__.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests for:

- planner callback returning a list of actions
- executor callback running actions sequentially
- loop returning the final output from the last completed action
- fallback behavior when no planner is configured

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k loop -v`
Expected: FAIL because `CodexAgentLoop` is missing.

**Step 3: Write minimal implementation**

Implement:

- `CodexContext`
- `CodexAgentLoop`
- `CodexAgentLoopResult`

Support an injected `action_planner` and `action_executor` through `context.services`. If no planner is present, fall back to one `respond` action using the original user input.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k loop -v`
Expected: PASS

### Task 3: Add artifact recording and approval pause

**Files:**
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests proving:

- inline artifact payloads are persisted through the configured artifact store
- high-risk or explicitly approval-gated actions return `needs_approval`
- `resume()` continues from the paused action after approval

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "artifact or approval" -v`
Expected: FAIL because the loop does not yet persist artifacts or resume paused tasks.

**Step 3: Write minimal implementation**

Use the existing artifact store interface. Keep approval storage local to the Codex loop for now to avoid coupling the new runtime to the old `ExecutionPlan` schema.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "artifact or approval" -v`
Expected: PASS

### Task 4: Start bridging profiles

**Files:**
- Modify: `docs/当前Agent设计框架.md`
- Modify: `docs/当前进展与改进建议.md`
- Create or modify: future profile integration files

**Step 1: Write the failing test**

Add integration tests only when a concrete profile bridge exists.

**Step 2: Run test to verify it fails**

Skip for now.

**Step 3: Write minimal implementation**

Document that `assistant` is now a profile/runtime, while `agents/codex` is the action-centric agent line.

**Step 4: Run test to verify it passes**

Run the relevant documentation-linked tests if added later.

## First Refactor Slice In This Session

This session should complete only:

- the design document
- the new Codex models
- the minimal Codex loop
- artifact persistence
- approval pause/resume
- tests for the above

That is enough to prove the direction without destabilizing the current desktop assistant.

## Verification

Run at minimum:

- `pytest tests/test_codex_agent.py -v`
- `pytest tests/test_assistant_runtime.py -v`

If package exports change, also run:

- `pytest tests/test_artifacts.py -v`

## Commit Strategy

1. Commit the design document and new Codex scaffolding.
2. Commit approval/artifact support and tests.
3. Keep future profile migration in separate commits.

## Expected Outcome

After this slice, the repository should support two parallel ideas:

- the existing capability-centric assistant runtime
- a new action-centric Codex runtime that can become the stronger execution profile over time
