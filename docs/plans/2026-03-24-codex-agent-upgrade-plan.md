# Codex Agent Upgrade Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade the current Codex-style agent from a single-step tool router into a stateful agent that keeps gathering evidence, tracks task memory, and only finishes when evidence is sufficient.

**Architecture:** Keep the existing `CodexAgentLoop` and `CodexTask` model, but add a stricter evaluator gate, structured task memory, richer tool result normalization, and a lightweight subgoal layer. The first implementation should stay local to the Codex runtime and avoid rewriting the assistant runtime. Each iteration is validated with focused failing tests first, then minimal implementation.

**Tech Stack:** Python 3.12, pytest, dataclasses, existing `agent_runtime_framework` loop/planner/tool abstractions.

### Task 1: Iteration 1 - Finish Gate And Continue Gate

**Files:**
- Modify: `agent_runtime_framework/agents/codex/evaluator.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Modify: `agent_runtime_framework/agents/codex/models.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

```python
def test_codex_evaluator_keeps_exploring_when_directory_listing_is_not_enough(...):
    ...

def test_codex_evaluator_blocks_finish_when_verification_is_pending(...):
    ...

def test_codex_evaluator_does_not_finish_plain_tool_output_without_claims(...):
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "keeps_exploring or verification_is_pending or plain_tool_output" -q`
Expected: FAIL because the current evaluator still finishes too early or allows direct respond too easily.

**Step 3: Write minimal implementation**

```python
def _can_finish(task: CodexTask) -> bool:
    return not task.memory.open_questions and not task.memory.pending_verifications
```

Add:
- explicit finish gate
- explicit continue gate
- tool-output-to-respond guard
- verification-required guard

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "keeps_exploring or verification_is_pending or plain_tool_output" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_codex_agent.py agent_runtime_framework/agents/codex/evaluator.py agent_runtime_framework/agents/codex/loop.py agent_runtime_framework/agents/codex/models.py
git commit -m "feat: add codex finish gates"
```

### Task 2: Iteration 2 - Structured Task Working Memory

**Files:**
- Modify: `agent_runtime_framework/agents/codex/models.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Create: `agent_runtime_framework/agents/codex/memory.py`
- Modify: `agent_runtime_framework/agents/codex/runtime.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

```python
def test_codex_task_memory_tracks_read_paths_and_modified_paths(...):
    ...

def test_codex_task_memory_tracks_open_questions_and_claims(...):
    ...

def test_codex_task_memory_tracks_pending_verifications_until_command_runs(...):
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "task_memory" -q`
Expected: FAIL because `CodexTask` does not yet expose structured memory or update it.

**Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class CodexTaskMemory:
    known_facts: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    read_paths: list[str] = field(default_factory=list)
    modified_paths: list[str] = field(default_factory=list)
    pending_verifications: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
```

Update task memory after each action using normalized tool output.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "task_memory" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_codex_agent.py agent_runtime_framework/agents/codex/models.py agent_runtime_framework/agents/codex/loop.py agent_runtime_framework/agents/codex/memory.py agent_runtime_framework/agents/codex/runtime.py
git commit -m "feat: add codex task working memory"
```

### Task 3: Iteration 3 - Agent-Friendly Tool Results

**Files:**
- Modify: `agent_runtime_framework/agents/codex/tools.py`
- Modify: `agent_runtime_framework/tools/executor.py`
- Modify: `agent_runtime_framework/agents/codex/planner.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Test: `tests/test_codex_agent.py`
- Test: `tests/test_tool_registry.py`

**Step 1: Write the failing test**

```python
def test_read_tool_returns_summary_truncated_and_next_hint(...):
    ...

def test_write_tool_returns_change_summary_and_modified_path(...):
    ...

def test_planner_prefers_agent_friendly_tool_metadata(...):
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py tests/test_tool_registry.py -k "next_hint or change_summary or agent_friendly" -q`
Expected: FAIL because current tools still mostly return raw `text` only.

**Step 3: Write minimal implementation**

Return structured tool payload fields:
- `summary`
- `truncated`
- `next_hint`
- `entities`
- `changed_paths`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py tests/test_tool_registry.py -k "next_hint or change_summary or agent_friendly" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_codex_agent.py tests/test_tool_registry.py agent_runtime_framework/agents/codex/tools.py agent_runtime_framework/agents/codex/planner.py agent_runtime_framework/agents/codex/loop.py agent_runtime_framework/tools/executor.py
git commit -m "feat: add agent-friendly codex tool outputs"
```

### Task 4: Iteration 4 - Subgoal Layer

**Files:**
- Modify: `agent_runtime_framework/agents/codex/models.py`
- Modify: `agent_runtime_framework/agents/codex/planner.py`
- Modify: `agent_runtime_framework/agents/codex/evaluator.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

```python
def test_codex_planner_assigns_subgoal_sequence_for_analysis_task(...):
    ...

def test_codex_evaluator_advances_subgoal_before_finish(...):
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "subgoal" -q`
Expected: FAIL because the current runtime is action-centric only.

**Step 3: Write minimal implementation**

Add:
- `subgoal` field on `CodexAction`
- planner mapping from task type to subgoal
- evaluator progress based on subgoal completion

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "subgoal" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_codex_agent.py agent_runtime_framework/agents/codex/models.py agent_runtime_framework/agents/codex/planner.py agent_runtime_framework/agents/codex/evaluator.py agent_runtime_framework/agents/codex/loop.py
git commit -m "feat: add codex subgoal planning"
```

### Task 5: Full Regression Verification

**Files:**
- Test: `tests/test_codex_agent.py`
- Test: `tests/test_assistant_runtime.py`
- Test: `tests/test_tool_registry.py`

**Step 1: Run focused Codex tests**

Run: `pytest tests/test_codex_agent.py tests/test_tool_registry.py -q`
Expected: PASS

**Step 2: Run assistant integration tests**

Run: `pytest tests/test_assistant_runtime.py -q`
Expected: PASS

**Step 3: Run targeted demo/runtime tests if touched**

Run: `pytest tests/test_demo_app.py -q`
Expected: PASS or note any unrelated pre-existing failures.

**Step 4: Document residual risk**

Record remaining gaps:
- no full context compaction pipeline yet
- no persistent branch/session tree yet
- no multi-subagent orchestration

**Step 5: Commit**

```bash
git add docs/plans/2026-03-24-codex-agent-upgrade-plan.md
git commit -m "docs: add codex agent upgrade implementation plan"
```
