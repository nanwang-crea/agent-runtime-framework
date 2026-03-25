# General Agent Upgrade Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将当前 Agent 从关键词驱动的特例流程，升级为基于任务语义、资源语义和恢复机制的通用 Agent。

**Architecture:** 第一阶段先稳定目录 / 代码库理解链路，补齐更宽松的任务识别和目录恢复；第二阶段将资源类型显式传播到 plan state；第三阶段再把 planner 和 evaluator 收敛到统一状态机。整个改造尽量复用现有 `CodexAgentLoop`、`task_plans` 和默认 codex tools，不额外引入新的执行平面。

**Tech Stack:** Python 3, pytest, dataclasses, current `agents/codex` runtime

### Task 1: Stabilize Repository Understanding Requests

**Files:**
- Modify: `agent_runtime_framework/agents/codex/profiles.py`
- Modify: `agent_runtime_framework/agents/codex/planner.py`
- Modify: `agent_runtime_framework/agents/codex/task_plans.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add a test for a natural query like `agent_runtime_framework这个目录下面都是在讲什么呢？` and assert:
- `task_profile == "repository_explainer"`
- first action uses `resolve_workspace_target`
- final answer contains key files

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "natural_directory_question" -q`
Expected: FAIL because the request falls back to `chat`

**Step 3: Write minimal implementation**

Implement broader repository-understanding markers and a shared target hint extractor that can pull ASCII path-like tokens from mixed Chinese text.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "natural_directory_question" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/agents/codex/profiles.py agent_runtime_framework/agents/codex/planner.py agent_runtime_framework/agents/codex/task_plans.py tests/test_codex_agent.py
git commit -m "feat: stabilize repository understanding requests"
```

### Task 2: Recover from Directory/File Tool Mismatch

**Files:**
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing test**

Add a test for `总结 docs` where `docs` is a directory and assert the demo returns a completed answer instead of `RESOURCE_IS_DIRECTORY`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_demo_app.py -k "directory_summarize_request" -q`
Expected: FAIL because the request currently returns a structured error

**Step 3: Write minimal implementation**

When `read_workspace_text` or `summarize_workspace_text` fails with `IsADirectoryError`, transparently recover to `inspect_workspace_path` or `list_workspace_directory`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_demo_app.py -k "directory_summarize_request" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/agents/codex/loop.py tests/test_demo_app.py
git commit -m "feat: recover from directory tool mismatches"
```

### Task 3: Externalize Resource Semantics into Plan State

**Files:**
- Modify: `agent_runtime_framework/resources/resolver.py`
- Modify: `agent_runtime_framework/agents/codex/models.py`
- Modify: `agent_runtime_framework/agents/codex/task_plans.py`
- Modify: `agent_runtime_framework/agents/codex/planner.py`
- Test: `tests/test_resource_resolver_pipeline.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add tests asserting the resolved target carries enough information to decide whether follow-up should `list`, `inspect`, or `read`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_resolver_pipeline.py tests/test_codex_agent.py -k "resource_semantics" -q`
Expected: FAIL because the current resolver only returns `ResourceRef`

**Step 3: Write minimal implementation**

Introduce explicit resource semantics metadata and propagate it into plan generation.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_resolver_pipeline.py tests/test_codex_agent.py -k "resource_semantics" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/resources/resolver.py agent_runtime_framework/agents/codex/models.py agent_runtime_framework/agents/codex/task_plans.py agent_runtime_framework/agents/codex/planner.py tests/test_resource_resolver_pipeline.py tests/test_codex_agent.py
git commit -m "feat: propagate resource semantics through codex plans"
```

### Task 4: Replace Evaluator Special Cases with State-Based Continuation

**Files:**
- Modify: `agent_runtime_framework/agents/codex/evaluator.py`
- Modify: `agent_runtime_framework/agents/codex/task_plans.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add a test that verifies continuation is chosen because evidence is insufficient, not because a specific tool name happened to appear.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "evidence_sufficiency" -q`
Expected: FAIL because continuation currently depends on a narrow tool-name branch

**Step 3: Write minimal implementation**

Move “need more evidence” logic to state-based checks using task profile, plan state, and resource kind.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "evidence_sufficiency" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/agents/codex/evaluator.py agent_runtime_framework/agents/codex/task_plans.py tests/test_codex_agent.py
git commit -m "feat: use state-based evidence continuation"
```

### Task 5: Unify Retriable Failure Recovery Into Plan Flow

**Files:**
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Modify: `agent_runtime_framework/agents/codex/task_plans.py`
- Modify: `MEMORY.md`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing test**

Add a test showing that when a retriable action exhausts retry budget, the loop should not just abort. Instead, it should:
- record the failed action,
- ask the planner/LLM for a recovery task,
- insert that task before `synthesize_answer`,
- continue execution.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_codex_agent.py -k "retriable_error_exhausts_retries" -q`
Expected: FAIL because `AppError` currently escapes before the plan can recover.

**Step 3: Write minimal implementation**

Normalize retriable `AppError` into structured failed results, then let `task_plans` insert a generic `recover_failed_action` task when the model can propose a single next action that keeps the task moving.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_codex_agent.py -k "retriable_action_error_once or does_not_retry_high_risk_action_error or failed_result_marked_retriable or retriable_error_exhausts_retries" -q`
Expected: PASS

**Step 5: Run regression verification**

Run:
- `pytest tests/test_codex_agent.py tests/test_resource_resolver_pipeline.py -q`
- `pytest tests/test_demo_app.py -k "directory_summarize_request or specialized_pattern or emits_single_delta_for_non_conversation_results" -q`

Expected: PASS

### Priority Roadmap

**P0: Intelligence-critical system layers**
- Searchable long-term memory
- Codebase indexing and semantic retrieval
- Follow-up reference resolution and clarification
- Unified planner/evaluator/retry/recover state machine
- Long-task context compaction

**P1: Execution-quality layers**
- Background asynchronous tasks
- Multi-agent / parallel exploration / isolated workspaces
- Environment snapshot and dependency setup
- Verify-fix-reverify loops
- Retrieval over repo history, PR context, and logs

**P2: Productization layers**
- Reviewable memory/rules evolution
- Better execution trace explainability
- More natural progress feedback
- Skill / workflow packaging

### Task 6: Introduce Searchable Agent Memory

**Files:**
- Modify: `agent_runtime_framework/memory/index.py`
- Modify: `agent_runtime_framework/memory/__init__.py`
- Modify: `agent_runtime_framework/agents/codex/tools.py`
- Test: `tests/test_memory_and_policy.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing tests**

Add tests asserting:
- `InMemoryIndexMemory` can store structured memory records and retrieve the most relevant ones by query.
- `resolve_workspace_target` prefers a remembered relevant workspace path when multiple candidates share the same basename.

**Step 2: Run tests to verify they fail**

Run:
- `pytest tests/test_memory_and_policy.py -k "relevance_search" -q`
- `pytest tests/test_codex_agent.py -k "memory_aware_target_resolution" -q`

Expected: FAIL because `index_memory` currently only supports plain `get/put`, and target resolution ignores stored memory.

**Step 3: Write minimal implementation**

Add a structured memory record type plus `remember/search` APIs, then persist workspace focus summaries into that memory and let `resolve_workspace_target` consult the retrieved records before falling back to heuristic path matching.

**Step 4: Run tests to verify they pass**

Run:
- `pytest tests/test_memory_and_policy.py -k "relevance_search" -q`
- `pytest tests/test_codex_agent.py -k "memory_aware_target_resolution" -q`

Expected: PASS

**Step 5: Run regression verification**

Run:
- `pytest tests/test_memory_and_policy.py tests/test_codex_agent.py tests/test_resource_resolver_pipeline.py -q`

Expected: PASS

### Task 7: Unify Target Resolution State and Clarification

**Files:**
- Modify: `agent_runtime_framework/resources/resolver.py`
- Modify: `agent_runtime_framework/resources/__init__.py`
- Modify: `agent_runtime_framework/agents/codex/tools.py`
- Modify: `agent_runtime_framework/agents/codex/task_plans.py`
- Modify: `MEMORY.md`
- Test: `tests/test_resource_resolver_pipeline.py`
- Test: `tests/test_codex_agent.py`

**Step 1: Write the failing tests**

Add tests asserting:
- the resource resolver can report `ambiguous` when multiple candidates match the same target hint;
- repository explanation requests turn ambiguity into a clarification response instead of silently choosing one candidate.

**Step 2: Run tests to verify they fail**

Run:
- `pytest tests/test_resource_resolver_pipeline.py -k "reports_ambiguity" -q`
- `pytest tests/test_codex_agent.py -k "asks_for_clarification_when_target_is_ambiguous" -q`

Expected: FAIL because the resolver currently only returns a single best-effort ref and the codex plan keeps executing.

**Step 3: Write minimal implementation**

Introduce explicit resolution state (`resolved / ambiguous / unresolved`), pass session focus and index-memory hints into that state machine, and let `task_plans` convert ambiguous locate results into a `clarify_target` response while preserving create-target flows.

**Step 4: Run tests to verify they pass**

Run:
- `pytest tests/test_resource_resolver_pipeline.py -k "reports_ambiguity" -q`
- `pytest tests/test_codex_agent.py -k "asks_for_clarification_when_target_is_ambiguous or creates_workspace_file_via_default_tooling" -q`

Expected: PASS

**Step 5: Run regression verification**

Run:
- `pytest tests/test_memory_and_policy.py tests/test_codex_agent.py tests/test_resource_resolver_pipeline.py -q`
- `pytest tests/test_demo_app.py -k "directory_summarize_request or specialized_pattern or emits_single_delta_for_non_conversation_results or memory_event" -q`

Expected: PASS

### Task 8: Persist Searchable Memory as Markdown Records

**Files:**
- Modify: `agent_runtime_framework/memory/index.py`
- Modify: `agent_runtime_framework/memory/__init__.py`
- Modify: `agent_runtime_framework/__init__.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_memory_and_policy.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing tests**

Add tests asserting:
- a markdown-backed index memory can persist `MemoryRecord` entries and reload them for search;
- the demo assistant app writes memory records into a workspace-local Markdown file after a successful workspace interaction.

**Step 2: Run tests to verify they fail**

Run:
- `pytest tests/test_memory_and_policy.py -k "markdown_index_memory_persists_records_and_searches_them" -q`
- `pytest tests/test_demo_app.py -k "persists_markdown_memory_records" -q`

Expected: FAIL because no markdown-backed implementation exists yet and the demo app still uses in-memory index memory.

**Step 3: Write minimal implementation**

Add a markdown-backed `IndexMemory` implementation that reuses the existing `remember/search` surface, persists structured records in a workspace-local Markdown file, and switch the demo app to use it without introducing embeddings or new storage dependencies.

**Step 4: Run tests to verify they pass**

Run:
- `pytest tests/test_memory_and_policy.py -k "markdown_index_memory_persists_records_and_searches_them" -q`
- `pytest tests/test_demo_app.py -k "persists_markdown_memory_records" -q`

Expected: PASS

**Step 5: Run regression verification**

Run:
- `pytest tests/test_memory_and_policy.py tests/test_codex_agent.py tests/test_resource_resolver_pipeline.py -q`
- `pytest tests/test_demo_app.py -k "directory_summarize_request or specialized_pattern or emits_single_delta_for_non_conversation_results or memory_event or persists_markdown_memory_records" -q`

Expected: PASS

### Task 9: Promote Markdown Memory Defaults and Recover Clarification Loops

**Files:**
- Modify: `agent_runtime_framework/applications/core.py`
- Modify: `agent_runtime_framework/assistant/conversation.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Modify: `agent_runtime_framework/agents/codex/task_plans.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_memory_and_policy.py`
- Test: `tests/test_codex_agent.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing tests**

Add tests asserting:
- `ApplicationContext` defaults to a workspace-local markdown-backed index memory when a workspace root is known;
- ambiguous repository-explainer requests return `needs_clarification` instead of `completed`;
- the next user turn can continue the original task after clarification rather than starting over;
- the demo app follows the same clarification loop across turns.

**Step 2: Run tests to verify they fail**

Run:
- `pytest tests/test_memory_and_policy.py -k "application_context_uses_markdown_index_memory_for_workspace_defaults" -q`
- `pytest tests/test_codex_agent.py -k "repository_explainer_asks_for_clarification_when_target_is_ambiguous or repository_explainer_can_resume_after_target_clarification" -q`
- `pytest tests/test_demo_app.py -k "resumes_clarification_loop_across_turns" -q`

Expected: FAIL because the default application context still uses in-memory index storage, and `clarify_target` still terminates the task instead of pausing it for recovery.

**Step 3: Write minimal implementation**

Make `ApplicationContext` choose a workspace-local markdown memory by default, promote repository-style module questions through the deterministic codex route, and let `CodexAgentLoop` store pending clarification tasks so the next user message resumes the original plan with the clarified target.

**Step 4: Run tests to verify they pass**

Run:
- `pytest tests/test_memory_and_policy.py -k "application_context_uses_markdown_index_memory_for_workspace_defaults" -q`
- `pytest tests/test_codex_agent.py -k "repository_explainer_asks_for_clarification_when_target_is_ambiguous or repository_explainer_can_resume_after_target_clarification" -q`
- `pytest tests/test_demo_app.py -k "resumes_clarification_loop_across_turns" -q`

Expected: PASS

**Step 5: Run regression verification**

Run:
- `pytest tests/test_memory_and_policy.py tests/test_codex_agent.py tests/test_resource_resolver_pipeline.py -q`
- `pytest tests/test_demo_app.py -k "directory_summarize_request or specialized_pattern or emits_single_delta_for_non_conversation_results or memory_event or persists_markdown_memory_records or resumes_clarification_loop_across_turns" -q`

Expected: PASS

### Task 10: Persist Clarification State Across Restart

**Files:**
- Modify: `agent_runtime_framework/memory/index.py`
- Modify: `agent_runtime_framework/agents/codex/loop.py`
- Modify: `MEMORY.md`
- Test: `tests/test_memory_and_policy.py`
- Test: `tests/test_codex_agent.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing tests**

Add tests asserting:
- markdown-backed index memory persists `put/get` runtime state across reloads;
- a persisted pending clarification lets a new `CodexAgentLoop` instance continue the original task after restart;
- the demo app can restart and still continue the same clarification loop.

**Step 2: Run tests to verify they fail**

Run:
- `pytest tests/test_memory_and_policy.py -k "markdown_index_memory_persists_values_across_reloads" -q`
- `pytest tests/test_codex_agent.py -k "repository_explainer_can_resume_after_restart_with_persisted_clarification" -q`
- `pytest tests/test_demo_app.py -k "resumes_clarification_after_restart" -q`

Expected: FAIL because markdown memory only persists searchable records, while pending clarification is still an in-process structure.

**Step 3: Write minimal implementation**

Persist runtime key/value state alongside markdown memory, mirror pending clarification into that state store, and restore it when a new loop instance starts on the same workspace.

**Step 4: Run tests to verify they pass**

Run:
- `pytest tests/test_memory_and_policy.py -k "markdown_index_memory_persists_values_across_reloads" -q`
- `pytest tests/test_codex_agent.py -k "repository_explainer_can_resume_after_restart_with_persisted_clarification" -q`
- `pytest tests/test_demo_app.py -k "resumes_clarification_after_restart" -q`

Expected: PASS

**Step 5: Run regression verification**

Run:
- `pytest tests/test_memory_and_policy.py tests/test_codex_agent.py tests/test_resource_resolver_pipeline.py -q`
- `pytest tests/test_demo_app.py -k "routes_normal_chat_to_conversation or directory_summarize_request or specialized_pattern or emits_single_delta_for_non_conversation_results or memory_event or persists_markdown_memory_records or resumes_clarification_loop_across_turns or resumes_clarification_after_restart" -q`

Expected: PASS
