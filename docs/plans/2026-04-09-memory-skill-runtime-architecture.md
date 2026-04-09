# Memory And Skill Runtime Architecture Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current over-layered workflow memory design with a cleaner four-part state architecture centered on transcript, session memory, working memory, and long-term memory, while introducing `SkillRuntime` and `MemoryManager` as the two new governance layers.

**Architecture:** Conversation transcript remains the raw source record. `Session Memory` becomes the short-term source of truth across turns. `Working Memory` is reduced to the smallest resumable task-local state for the current workflow run. `Long-term Memory` stores only stable project and user knowledge. All capability execution goes through `SkillRuntime`, and all memory writes go through `MemoryManager`.

**Tech Stack:** Python dataclasses, existing workflow runtime, existing memory modules, existing tool registry, MCP adapters, pytest.

## Target Architecture

```text
Conversation Transcript
        +
Session Memory
        +
Long-term Memory
        ↓
Task Snapshot Builder
        ↓
Workflow Engine
  ├── Planner
  ├── Judge
  ├── Executors
  ├── Working Memory
  └── SkillRuntime
        ↓
Tools / MCP / APIs
        ↓
MemoryManager
        ↓
Session Memory / Long-term Memory
```

## Core Rules

- Transcript stores raw conversation turns and is never treated as memory.
- Session memory is the short-term source of truth for cross-turn references such as "the file we just created".
- Working memory only stores the minimum task-local state needed to advance or resume the current workflow run.
- Long-term memory stores stable preferences, conventions, and aliases only.
- Skills, MCP tools, and local tools do not write memory directly.
- All memory updates must flow through `MemoryManager`.
- Old memory code that is no longer useful should be deleted rather than preserved for inertia.
- Tests must be updated to fit the new architecture; production design must not be bent to satisfy obsolete test seams.

## Data Layer Design

### 1. Conversation Transcript

**Purpose**

- Preserve raw turns for replay, display, and snapshot rebuilding.
- Keep a faithful conversation log independent from derived memory.

**Suggested model**

```python
@dataclass(slots=True)
class ConversationTurn:
    role: str
    content: str
    run_id: str | None = None
```

**Notes**

- Continue using `session.turns` as the transport shape.
- Transcript is append-only conversation state, not planner state.

### 2. Session Memory

**Purpose**

- Handle cross-turn references and recent workspace focus.
- Act as the short-term source of truth outside a single workflow run.

**Suggested model**

```python
@dataclass(slots=True)
class SessionMemoryState:
    last_active_target: str | None = None
    recent_paths: list[str] = field(default_factory=list)
    last_action_summary: str | None = None
    last_read_files: list[str] = field(default_factory=list)
    last_clarification: dict[str, Any] | None = None
```

**Notes**

- This layer should absorb what the current system stores implicitly through `remember_focus`.
- This is the layer that should answer "just now", "previous file", and "that document".

### 3. Working Memory

**Purpose**

- Store the smallest resumable task-local state for the current workflow run.
- Be weakly persistent and always safe to recompute.

**Suggested model**

```python
@dataclass(slots=True)
class WorkingMemory:
    active_target: str | None = None
    confirmed_targets: list[str] = field(default_factory=list)
    excluded_targets: list[str] = field(default_factory=list)
    current_step: str | None = None
    open_issues: list[str] = field(default_factory=list)
    last_tool_result_summary: dict[str, Any] | None = None
```

**Explicitly remove from working memory**

- `clarification_memory`
- `semantic_memory`
- `execution_memory`
- `preference_memory`
- `search_plan`
- `read_plan`
- `verification_state`
- `recovery_notes`
- large evidence summaries
- full reasoning traces

**Rationale**

- These values are either transient reasoning artifacts or recomputable plans.
- Keeping them as persistent memory makes resume less trustworthy, not more trustworthy.

### 4. Long-term Memory

**Purpose**

- Store stable, low-frequency knowledge that should survive beyond the current session.

**Suggested shape**

```python
{
  "user_preferences": {...},
  "project_conventions": {...},
  "path_aliases": {...}
}
```

**Notes**

- This layer should remain deliberately small.
- Write frequency must stay low.

## Unified Context Layer

### 5. Task Snapshot Builder

**Create**

- `agent_runtime_framework/workflow/memory/task_snapshot.py`

**Suggested model**

```python
@dataclass(slots=True)
class TaskSnapshot:
    goal: str
    recent_focus: list[str]
    recent_paths: list[str]
    last_action_summary: str | None
    last_clarification: dict[str, Any] | None
    long_term_hints: dict[str, Any]
```

**Rules**

- Snapshot must be trimmed before being passed to planner or semantic nodes.
- Do not feed full transcript or full memory blobs into the model.
- Snapshot exists to provide a single, coherent, small context payload.

**Explicit note**

- Add a snapshot trimming function and make it mandatory.
- No node should receive raw full transcript plus raw session memory plus raw long-term memory directly.

## Governance Layer

### 6. MemoryManager

**Create**

- `agent_runtime_framework/workflow/memory/manager.py`

**Suggested interface**

```python
class MemoryManager:
    def build_task_snapshot(self, *, session_memory, long_term_memory, transcript) -> TaskSnapshot: ...
    def init_working_memory(self, snapshot: TaskSnapshot) -> WorkingMemory: ...
    def checkpoint_working_memory(self, working_memory: WorkingMemory) -> dict[str, Any]: ...
    def restore_working_memory(self, payload: dict[str, Any]) -> WorkingMemory: ...

    def update_session_from_tool_result(self, result: dict[str, Any]) -> None: ...
    def update_session_from_clarification(self, clarification: dict[str, Any]) -> None: ...
    def update_session_from_final_response(self, response: dict[str, Any]) -> None: ...

    def update_long_term_if_needed(self, event: dict[str, Any]) -> None: ...
```

**Rules**

- Workflow nodes must not write session memory directly.
- Tools must not write session or long-term memory directly.
- Clarification completion writes through `MemoryManager`.
- Final answer completion writes through `MemoryManager`.

### 7. SkillRuntime

**Create**

- `agent_runtime_framework/skills/runtime.py`

**Suggested structures**

```python
@dataclass(slots=True)
class SkillResult:
    name: str
    success: bool
    summary: str
    payload: dict[str, Any]
    changed_paths: list[str]
    references: list[str]
    memory_hint: dict[str, Any] | None = None

class SkillRuntime:
    def invoke(self, skill_name: str, input: dict[str, Any], context: Any) -> SkillResult: ...
```

```python
class SkillProvider(Protocol):
    def invoke(self, skill_name: str, input: dict[str, Any], context: Any) -> SkillResult: ...
```

**Rules**

- Skills do not write memory.
- Skills return `memory_hint` only.
- `MemoryManager` decides whether any `memory_hint` should update session or long-term memory.
- MCP should be wired in as a `SkillProvider`, not as a direct workflow dependency.

## Integration With Existing Code

### 8. Collapse workflow state memory

**Modify**

- `agent_runtime_framework/workflow/state/models.py`

**Change**

- Replace `WorkflowMemoryState.clarification_memory`
- Replace `WorkflowMemoryState.semantic_memory`
- Replace `WorkflowMemoryState.execution_memory`
- Replace `WorkflowMemoryState.preference_memory`

with a single `working_memory`.

### 9. Simplify memory views

**Modify**

- `agent_runtime_framework/workflow/memory/views.py`

**Keep only**

- `build_task_snapshot_view(...)`
- `build_working_memory_view(...)`
- `build_response_context_view(...)`

**Delete or reduce**

- `build_planner_memory_view(...)`
- `build_semantic_memory_view(...)`
- `build_judge_memory_view(...)`

These may temporarily remain as thin compatibility wrappers during migration, but the final state should not rely on them as independent memory concepts.

### 10. Make semantic nodes consume snapshot + working memory

**Modify**

- `agent_runtime_framework/workflow/nodes/semantic.py`

**Priority targets**

- `InterpretTargetExecutor`
- `PlanSearchExecutor`
- `PlanReadExecutor`

**Rules**

- `InterpretTargetExecutor` must consume task snapshot first.
- `fallback_hint` must be derived from session memory recent focus.
- Cross-turn references such as "the file just created" must resolve through session memory before asking the model to guess.

### 11. Stop tools from writing memory directly

**Modify**

- `agent_runtime_framework/workflow/workspace/tools/common.py`
- `agent_runtime_framework/workflow/workspace/tools/file_tools.py`

**Change**

- Tool layers produce structured results only.
- Service/workflow layers call `MemoryManager.update_session_from_tool_result(...)`.

**Delete**

- Direct tool-to-memory writes such as implicit focus recording inside tool helper code once the manager path is in place.

### 12. MCP integration

**Use current module**

- `agent_runtime_framework/mcp/registry.py`

**Approach**

- Treat MCP adapters as `SkillProvider` implementations.
- Planner and judge should only see normalized skill specs.

## Persistence Strategy

### 13. Persist only these layers

- transcript
- session memory
- long-term memory
- working memory checkpoint

### 14. Never persist these runtime artifacts

- `runtime_context`
- full tool outputs
- full repair history
- full reasoning traces
- large evidence chunks
- search/read/verification plans that can be recomputed

**Working memory checkpoint shape**

```python
{
  "active_target": ...,
  "confirmed_targets": ...,
  "excluded_targets": ...,
  "current_step": ...,
  "open_issues": ...,
  "last_tool_result_summary": ...,
}
```

## Resume Strategy

### 15. Resume merge rules

**Recovery order**

1. transcript
2. session memory
3. long-term memory
4. working memory checkpoint
5. rebuild task snapshot
6. validate working memory

**Hard rule**

- Resume must validate working memory before using it.

Suggested behavior:

```python
if working_memory.active_target:
    validate_against_session_memory_and_workspace()
else:
    re_interpret_target()
```

**Meaning**

- Working memory is an accelerator, not the truth.
- Session memory and actual workspace state override stale working memory.

## Migration Phases

### Phase 1

- Define `WorkingMemory`
- Define `SessionMemoryState`
- Create `MemoryManager`
- Create `TaskSnapshot`

### Phase 2

- Update `InterpretTargetExecutor` to consume snapshot
- Fix cross-turn target carryover such as "delete the file just created"
- Reduce workflow persistence to working memory checkpoint only

### Phase 3

- Introduce `SkillRuntime`
- Wrap local tools as skill providers
- Wrap MCP as skill providers

### Phase 4

- Route all memory writes through `MemoryManager`
- Delete obsolete workflow memory buckets and memory views
- Keep long-term memory low-frequency only

## Acceptance Criteria

- "Create `testes.txt`" followed by "delete the file just created" succeeds.
- "Read README" followed by "explain the document we just read" succeeds.
- Clarification decisions carry across turns through session memory.
- Workflow resume validates working memory and avoids stale target misuse.
- Tool, skill, and MCP layers never write memory directly.
- Snapshot payloads are trimmed before model use.
- Skill results return `memory_hint` only.
- Working memory is validated before resume use.
- Current useless memory code is removed rather than preserved for inertia.
- Old tests are updated to match the new architecture instead of freezing old memory structure.

## Explicit Cleanup Policy

- Delete memory code that no longer has runtime value.
- Do not retain redundant workflow memory buckets just because tests once relied on them.
- When tests encode obsolete architecture, update or delete the tests instead of distorting production code to keep them passing.

## Summary

The final architecture should converge to:

- Transcript for raw facts
- Session Memory for short-term truth
- Working Memory for minimal per-run state
- Long-term Memory for stable knowledge
- SkillRuntime for capability invocation
- MemoryManager for all state writes

This is the target shape for the next round of workflow and memory refactoring.
