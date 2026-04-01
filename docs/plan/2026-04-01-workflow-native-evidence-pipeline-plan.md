# Workflow Native Evidence Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current summary-first native workflow path with a multi-node evidence-driven pipeline that discovers workspace context, searches relevant targets, reads file chunks, synthesizes findings from evidence, and reports explicit verification state.

**Architecture:** Keep the existing `WorkflowNode` / `NodeResult` runtime shell, but replace the old native node pair (`repository_explainer`, `file_reader`) with a decomposed chain: `workspace_discovery -> content_search -> chunked_file_read -> aggregate_results -> evidence_synthesis -> verification -> final_response`. Preserve backward compatibility during migration by adding the new native executors first, teaching the graph builder to emit them, and only then retiring the old node types from deterministic graph generation.

**Tech Stack:** Python 3.12, dataclasses, existing workflow runtime/scheduler/persistence stack, `synthesize_text`, current demo app executor wiring, existing pytest suite.

### Task 1: Define the new native output contract

**Files:**
- Modify: `agent_runtime_framework/workflow/models.py`
- Modify: `agent_runtime_framework/workflow/aggregator.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing test**

Add or extend tests to assert that native node outputs can carry structured workflow data, not only plain summaries. Cover these fields inside `NodeResult.output` payloads produced by aggregation and final synthesis inputs:
- `summary`
- `facts`
- `evidence_items`
- `artifacts`
- `open_questions`
- `verification`

**Step 2: Run test to verify current baseline fails for the new contract**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_graph_builder.py -q`
Expected: FAIL because aggregation currently only preserves `summaries` and verification semantics are too weak.

**Step 3: Write minimal implementation**

Keep `NodeResult` as-is structurally to avoid wide compatibility churn, but document and enforce a normalized `output` shape in code paths that produce native workflow results. Add aggregation helpers that preserve:
- `summaries`
- `facts`
- `evidence_items`
- `references`
- `verification_events`
- `open_questions`
- `artifacts`

Do not add a large new type hierarchy yet; keep the shape simple and dict-based.

**Step 4: Run targeted tests**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_graph_builder.py -q`
Expected: PASS for new payload-shape assertions, with old behavior still covered where intentionally preserved.

### Task 2: Rebuild aggregation and verification semantics

**Files:**
- Modify: `agent_runtime_framework/workflow/aggregator.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing test**

Add tests that cover:
- aggregation deduplicates and merges `facts`, `evidence_items`, and `references`
- verification returns `not_run` when no verification event exists
- verification returns `failed` if any upstream verification result fails
- verification returns `passed` only when explicit upstream verification events all pass

**Step 2: Run test to verify the failure**

Run: `pytest tests/test_workflow_runtime.py -q`
Expected: FAIL because `VerificationExecutor` currently defaults to success and aggregation only gathers summaries.

**Step 3: Write minimal implementation**

Refactor `aggregate_node_results()` to merge structured fields instead of flattening to summaries only. Refactor `VerificationExecutor` so it consumes merged verification events and produces a three-state result:
- `status: passed`
- `status: failed`
- `status: not_run`

Retain `summary` for display, but never treat missing verification as success.

**Step 4: Run targeted tests**

Run: `pytest tests/test_workflow_runtime.py -q`
Expected: PASS.

### Task 3: Add `workspace_discovery` native executor

**Files:**
- Create: `agent_runtime_framework/workflow/discovery_executor.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing test**

Add tests that assert a `workspace_discovery` node:
- scans the workspace root and common source directories
- emits `evidence_items` for candidate paths
- emits `facts` such as likely entrypoints, config files, source roots, and test roots
- can be wired into a workflow graph and executed by the demo/runtime executor registry

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_graph_builder.py -q`
Expected: FAIL because `workspace_discovery` does not exist yet in the executor registry or graph builder.

**Step 3: Write minimal implementation**

Implement `WorkspaceDiscoveryExecutor` with these rules:
- root-level scan is complete for the root directory
- common code directories are shallow-scanned (`src`, `app`, `agent_runtime_framework`, `tests`, `docs`, `frontend-shell` when present)
- candidates are ranked lightly using the goal text and path names
- output preserves `artifacts.tree_sample`, `facts`, `evidence_items`, and a concise `summary`

Do not introduce a persistent repo index in this task.

**Step 4: Run targeted tests**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_graph_builder.py -q`
Expected: PASS for the new node execution and graph wiring.

### Task 4: Add `content_search` native executor

**Files:**
- Create: `agent_runtime_framework/workflow/content_search_executor.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing test**

Add tests that assert `content_search` can:
- search candidate paths from upstream discovery output
- match path names and simple text terms derived from `run.goal`
- rank target files and produce `search_hit` evidence entries
- pass selected targets to downstream read nodes via `output`

**Step 2: Run test to verify the failure**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_graph_builder.py -q`
Expected: FAIL because there is no `content_search` executor or graph emission yet.

**Step 3: Write minimal implementation**

Implement `ContentSearchExecutor` with an intentionally simple first version:
- derive lightweight search terms from `run.goal` and optional `node.metadata`
- prioritize exact target hints from metadata
- search text only in a bounded set of candidate files from discovery output
- emit `ranked_targets`, `matches`, `evidence_items`, and `summary`

Avoid shelling out to `rg` in the first pass; stay within Python and existing runtime constraints.

**Step 4: Run targeted tests**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_graph_builder.py -q`
Expected: PASS.

### Task 5: Add `chunked_file_read` native executor

**Files:**
- Create: `agent_runtime_framework/workflow/chunked_file_read_executor.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing test**

Add tests that assert `chunked_file_read`:
- reads full content for small files
- reads windows around search hits for large files
- emits chunk metadata with line ranges
- preserves visible content in chunks rather than one monolithic truncated string
- produces `evidence_items` of kind `file_chunk`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_runtime.py -q`
Expected: FAIL because the existing `FileReadExecutor` only returns one truncated content blob.

**Step 3: Write minimal implementation**

Implement `ChunkedFileReadExecutor` with these behaviors:
- `full_if_small` for small files under a configurable threshold
- `windowed_by_hits` when `content_search` has matches
- `head_tail` fallback for large files with no hits
- line-number-aware chunk metadata
- `summary` generated from selected chunks, not from a blunt prefix slice

Keep the implementation text-based; do not introduce AST parsing in this task.

**Step 4: Run targeted tests**

Run: `pytest tests/test_workflow_runtime.py -q`
Expected: PASS.

### Task 6: Add `evidence_synthesis` native executor

**Files:**
- Create: `agent_runtime_framework/workflow/evidence_synthesis_executor.py`
- Modify: `agent_runtime_framework/workflow/__init__.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing test**

Add tests that assert `evidence_synthesis`:
- reads structured aggregate output instead of raw files
- produces a coherent `summary`
- emits `facts` and `open_questions`
- can feed `final_response` without depending on the old `response_synthesis` path

**Step 2: Run test to verify the failure**

Run: `pytest tests/test_workflow_runtime.py tests/test_demo_app.py -q`
Expected: FAIL because this node type does not exist and final response currently depends on old summary flow.

**Step 3: Write minimal implementation**

Implement `EvidenceSynthesisExecutor` that:
- consumes aggregated `facts`, `chunks`, `evidence_items`, and `references`
- uses `synthesize_text` to generate a concise evidence-grounded summary
- emits `open_questions` when evidence is sparse or conflicting
- stores synthesis output in `run.shared_state` for `final_response`

Do not mix file IO into this executor.

**Step 4: Run targeted tests**

Run: `pytest tests/test_workflow_runtime.py tests/test_demo_app.py -q`
Expected: PASS.

### Task 7: Teach the graph builder the new native pipeline

**Files:**
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Modify: `agent_runtime_framework/workflow/decomposition.py`
- Modify: `agent_runtime_framework/agents/workspace_backend/planner.py`
- Test: `tests/test_workflow_graph_builder.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing test**

Add graph-builder tests that assert deterministic native graphs become:
- repository overview: `workspace_discovery -> evidence_synthesis -> final_response`
- file explanation: `content_search -> chunked_file_read -> evidence_synthesis -> final_response`
- compound read/explain: `workspace_discovery -> content_search -> chunked_file_read -> aggregate_results -> evidence_synthesis -> final_response`
- verification-required flows insert `verification` after synthesis or aggregation as appropriate

Also add compatibility tests for model-generated graph normalization to accept the new native node types.

**Step 2: Run test to verify the failure**

Run: `pytest tests/test_workflow_graph_builder.py tests/test_demo_app.py -q`
Expected: FAIL because `_NATIVE_NODE_TYPES`, supported intents, and deterministic graph generation still emit old node types.

**Step 3: Write minimal implementation**

Update:
- `_NATIVE_NODE_TYPES`
- graph-builder prompt text for model-generated graphs
- deterministic graph assembly for repository/file/compound intents
- planner capability hints if needed so UI plan history remains sensible

Keep old node types accepted during migration, but stop emitting them by default.

**Step 4: Run targeted tests**

Run: `pytest tests/test_workflow_graph_builder.py tests/test_demo_app.py -q`
Expected: PASS.

### Task 8: Rewire final response generation to evidence-first synthesis

**Files:**
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write the failing test**

Add tests that assert `FinalResponseExecutor`:
- prefers output from `evidence_synthesis`
- falls back to aggregated evidence-based summaries when synthesis output is absent
- includes verification state awareness in final response generation inputs
- no longer depends on one summary string per upstream node

**Step 2: Run test to verify the failure**

Run: `pytest tests/test_workflow_runtime.py tests/test_demo_app.py -q`
Expected: FAIL because `FinalResponseExecutor` still assumes `summaries` are the main upstream signal.

**Step 3: Write minimal implementation**

Refactor final response generation so it consumes:
- synthesis summary when present
- otherwise aggregated `facts`, `evidence_items`, `references`, and verification state

Keep the response concise and user-facing, but derive it from evidence-rich upstream state.

**Step 4: Run targeted tests**

Run: `pytest tests/test_workflow_runtime.py tests/test_demo_app.py -q`
Expected: PASS.

### Task 9: Preserve compatibility and retire old native defaults

**Files:**
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Test: `tests/test_demo_app.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing test**

Add compatibility coverage for:
- old node types still executing if loaded from persisted/model-generated graphs
- new deterministic graphs no longer emitting `repository_explainer` or `file_reader` by default
- demo app executor registry containing both new and legacy native executors during the migration window

**Step 2: Run test to verify the failure**

Run: `pytest tests/test_demo_app.py tests/test_workflow_graph_builder.py -q`
Expected: FAIL until compatibility aliases and default graph behavior are both in place.

**Step 3: Write minimal implementation**

Keep legacy executors registered for compatibility, but route new graph generation to the new pipeline. Add a clear compatibility comment or helper mapping rather than deleting old implementations immediately.

**Step 4: Run targeted tests**

Run: `pytest tests/test_demo_app.py tests/test_workflow_graph_builder.py -q`
Expected: PASS.

### Task 10: Run verification and document follow-up cleanup

**Files:**
- Modify only if verification exposes real regressions
- Optional follow-up docs note in: `docs/architecture/` if a short architecture note is needed

**Step 1: Run focused workflow and demo verification**

Run: `pytest tests/test_workflow_graph_builder.py tests/test_workflow_runtime.py tests/test_demo_app.py -q`
Expected: PASS.

**Step 2: Run broader workflow-adjacent smoke tests**

Run: `pytest tests/test_workflow_approval.py tests/test_workflow_codex_subtask.py tests/test_memory_and_policy.py -q`
Expected: PASS.

**Step 3: Run full suite if the focused suite is clean**

Run: `pytest -q`
Expected: PASS or only unrelated pre-existing failures.

**Step 4: Record cleanup items**

If legacy node types remain in compatibility mode, document a follow-up cleanup list:
- remove `repository_explainer` from deterministic graphs entirely
- remove `file_reader` from deterministic graphs entirely
- decide whether old executor classes stay as aliases or get deleted
- consider extracting a shared evidence schema helper if duplication emerges

## Notes for the implementing engineer

- Keep changes incremental; do not try to replace the `target_resolution -> file_inspection -> response_synthesis` path in the same change unless tests prove it is safe.
- Prefer compatibility over purity in the first iteration; a clean migration beats a sweeping rewrite.
- Reuse the `workspace_subtask` evidence style where practical so native and fallback paths converge on similar payloads.
- Do not add repo indexing, AST parsing, or external search dependencies in the initial implementation.
- If a test reveals the need for a small shared helper for evidence merging or line-window calculation, add it only after the second concrete use appears.
