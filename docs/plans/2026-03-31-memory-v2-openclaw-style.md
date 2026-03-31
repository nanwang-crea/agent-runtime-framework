# Memory V2 OpenClaw Style Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild agent memory and target-resolution around an OpenClaw-style layered memory model so low-quality summaries can no longer pollute future task routing or target resolution.

**Architecture:** Separate user-facing answers from system memory, split memory into layered stores with explicit write policy, add entity-binding memory for high-confidence path aliases, and make resolver hints consume only controlled memory layers. Resolver fallback must become intent-aware so file-reading tasks never silently degrade to workspace-root resolution.

**Tech Stack:** Python 3.12, existing Codex agent runtime, in-memory/index memory abstractions, pytest.

## Background

Current failures come from a single bad loop:

1. A low-quality answer like `Found 18 entries.` is synthesized.
2. That answer is persisted as `task_conclusion` / `workspace_fact`.
3. Resolver hint lookup reads it back as if it were durable truth.
4. Future file requests get pulled toward `.` or a generic workspace target.

This plan adopts an OpenClaw-style split between:

- canonical memory
- daily process memory
- retrievable entity bindings
- weak derived recall

The implementation should ensure that:

- not every answer becomes memory
- not every memory becomes resolver input
- not every resolver miss falls back to `.`

## Design Summary

### Memory Layers

Introduce four conceptual layers:

1. `core_memory`
   - Stable facts, user preferences, durable decisions.
   - Human-reviewable, long-lived.

2. `daily_log`
   - Transient work log and low-confidence observations.
   - Safe to retain for traceability, unsafe for target resolution by default.

3. `entity_memory`
   - High-confidence alias-to-path bindings such as `README -> README.md`.
   - Primary resolver hint layer after explicit/sessional context.

4. `derived_index`
   - Retrieval-only support index over the above layers.
   - Must not invent new truth on its own.

### Memory Record Shape

Unify stored memory with explicit metadata:

```python
MemoryItem(
    memory_id: str,
    layer: Literal["core", "daily", "entity", "derived"],
    record_kind: Literal["observation", "summary", "decision", "entity_binding", "preference"],
    scope: Literal["workspace", "path", "entity", "session"],
    path: str,
    entity_name: str,
    entity_type: Literal["file", "directory", "module", "workspace", "unknown"],
    text: str,
    confidence: float,
    source_tool: str,
    source_task_profile: str,
    created_at: str,
    last_verified_at: str,
    retrievable_for_resolution: bool,
)
```

### Resolver Priority

Resolver should use this priority order:

1. Explicit path from user input
2. Session focus / recent focused resource
3. `entity_memory`
4. Live workspace search
5. Weak memory snippets from `daily_log` / `core_memory`
6. Clarify or unresolved

Never use `task_conclusion` text directly as a strong resolver hint.

### Fallback Rules

- File-oriented tasks:
  - If no high-confidence file candidate exists, return `ambiguous` or `unresolved`
  - Never fallback to `.`

- Directory / workspace tasks:
  - Allow `.` fallback only when user intent explicitly targets current workspace/root

- Mixed/unclear tasks:
  - Prefer clarify over workspace-root fallback

## Task 1: Add Layered Memory Schema

**Files:**
- Create: `agent_runtime_framework/agents/codex/memory_schema.py`
- Modify: `agent_runtime_framework/agents/codex/memory.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests that assert:

- low-information observations are marked non-retrievable for resolution
- entity bindings are marked retrievable for resolution
- directory-count summaries do not become durable resolver hints

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -q -k "memory_schema or entity_binding"`

Expected: FAIL because layered schema and policies do not yet exist.

**Step 3: Write minimal implementation**

Implement a small schema module with:

- `MemoryLayer`
- `MemoryRecordKind`
- `ResolverHintEligibility`
- helper constructor/normalizer

Update `memory.py` so extraction can produce richer memory records instead of only free-form strings.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -q -k "memory_schema or entity_binding"`

Expected: PASS

## Task 2: Introduce Memory Write Policy

**Files:**
- Create: `agent_runtime_framework/agents/codex/memory_policy.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Modify: `agent_runtime_framework/agents/codex/memory.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests that assert:

- `Found 18 entries.` can be written only to `daily_log`
- synthesized directory answers do not enter resolver-eligible memory
- only high-confidence file/module alias bindings become resolver-eligible

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -q -k "memory_write_policy"`

Expected: FAIL because all memories are still written indiscriminately.

**Step 3: Write minimal implementation**

Add a `MemoryWriteDecision` policy object:

```python
MemoryWriteDecision(
    allow_write: bool,
    target_layer: str,
    confidence: float,
    retrievable_for_resolution: bool,
    reason: str,
)
```

Policy rules:

- generic directory counts -> `daily`, not retrievable
- user-facing summaries -> `drop` or `daily`
- verified alias/path bindings -> `entity`, retrievable
- explicit durable user/system decisions -> `core`, retrievable when appropriate

Wire `_remember_completed_task()` to use the policy instead of persisting everything unconditionally.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -q -k "memory_write_policy"`

Expected: PASS

## Task 3: Add Entity Binding Memory

**Files:**
- Create: `agent_runtime_framework/agents/codex/entity_memory.py`
- Modify: `agent_runtime_framework/agents/codex/memory.py`
- Modify: `agent_runtime_framework/agents/codex/tools.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests that assert:

- `README` resolves to `README.md` when an entity binding exists
- `auth module` resolves to `src/auth.py` when bound
- entity bindings outrank generic `.` memory hints

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -q -k "entity_binding_resolution"`

Expected: FAIL because resolver has no dedicated entity-binding layer.

**Step 3: Write minimal implementation**

Create a small entity binding store abstraction:

- normalize alias
- store canonical path, entity type, confidence, evidence refs
- query by alias

Wire resolver hint generation to consult entity memory before general memory search.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -q -k "entity_binding_resolution"`

Expected: PASS

## Task 4: Rewrite Resolver Hint Policy

**Files:**
- Create: `agent_runtime_framework/agents/codex/resolver_hint_policy.py`
- Modify: `agent_runtime_framework/agents/codex/tools.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests that assert:

- `task_conclusion` is not used as a strong hint source
- `workspace_fact` with `path="."` is down-ranked
- `workspace_focus` and `entity_memory` outrank all summary-like hints

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -q -k "resolver_hint_policy"`

Expected: FAIL because resolver still reads raw `task_conclusion` / `workspace_fact`.

**Step 3: Write minimal implementation**

Implement weighted hint sources:

- `workspace_focus`: highest
- `entity_memory`: high
- `core_memory`: medium
- `daily_log`: low
- generic `path="."` summaries: near-zero or ignored

Only emit `ResolveHint` for resolver-eligible memory records.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -q -k "resolver_hint_policy"`

Expected: PASS

## Task 5: Replace Generic Resolver Fallback With Intent-Aware Fallback

**Files:**
- Modify: `agent_runtime_framework/agents/codex/tools.py`
- Modify: `agent_runtime_framework/agents/codex/semantics.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests that assert:

- `file_reader` + `README` never resolves to `.`
- workspace-root fallback only occurs for explicit current-directory requests
- ambiguous file-like requests produce clarify/unresolved instead of root fallback

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -q -k "resolver_fallback"`

Expected: FAIL because current fallback still defaults to `.`

**Step 3: Write minimal implementation**

Split fallback by task intent:

- `file_reader`: unresolved/clarify
- `repository_explainer` with explicit workspace markers: `.`
- ambiguous mixed requests: unresolved/clarify

Do not let generic memory hints override explicit file semantics.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -q -k "resolver_fallback"`

Expected: PASS

## Task 6: Separate Answer Memory Extraction From User Answer Generation

**Files:**
- Create: `agent_runtime_framework/agents/codex/memory_extractor.py`
- Modify: `agent_runtime_framework/agents/codex/answer_synthesizer.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests that assert:

- final answers are not directly persisted as resolver-eligible memory
- memory extraction operates on evidence/tool outputs, not synthesized prose
- directory listing answers do not create durable path bindings unless explicit evidence exists

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -q -k "memory_extractor"`

Expected: FAIL because final user answers still flow too directly into stored memory.

**Step 3: Write minimal implementation**

Create a `MemoryExtractor` that consumes:

- tool outputs
- structured evidence
- verified entity/path relations

and produces layered memory candidates. Keep `AnswerSynthesizer` user-facing only.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -q -k "memory_extractor"`

Expected: PASS

## Task 7: Add Quality Filters For Repository Summaries

**Files:**
- Modify: `agent_runtime_framework/agents/codex/answer_synthesizer.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests that assert:

- `目录结构：Found 18 entries.` is no longer emitted verbatim
- directory answers prefer named entries over count-only summaries
- repeated low-information lines like `条目：Found 18 entries.` are filtered out

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -q -k "repository_summary_quality"`

Expected: FAIL because current answer synthesis still leaks low-information machine summaries.

**Step 3: Write minimal implementation**

Add low-information filters:

- suppress count-only duplicate lines
- normalize directory/file listing into readable bullets
- prefer actual entry names over generic counts

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -q -k "repository_summary_quality"`

Expected: PASS

## Task 8: Add Regression Tests For The Original Failure Pattern

**Files:**
- Modify: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Create end-to-end regression cases that reproduce the current failure mode:

1. Ask for current directory listing
2. Persist resulting memory
3. Ask for `README`
4. Verify target resolves to `README.md`, not `.`

Add variants:

- `README`
- `根目录 README`
- `auth module`
- `service docs`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -q -k "memory_pollution_regression"`

Expected: FAIL before the full memory redesign is wired through.

**Step 3: Write minimal implementation**

No new implementation in this task beyond finishing previous tasks and wiring them together.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -q -k "memory_pollution_regression"`

Expected: PASS

## Final Verification

Run:

```bash
pytest -q tests/test_codex_agent.py -q
```

Expected:

- All existing Codex agent tests pass
- New memory-v2 tests pass
- Resolver no longer uses low-quality summary memory to select `.` for file tasks

## Notes

- Keep this implementation incremental. Do not redesign unrelated planner/evaluator components during Memory V2 work.
- Prefer adapting existing `index_memory`/`MemoryRecord` patterns instead of introducing a new storage backend immediately.
- If storage migration is needed, keep a compatibility layer first and only remove legacy `task_conclusion` / `workspace_fact` usage from resolver hints after tests are green.
