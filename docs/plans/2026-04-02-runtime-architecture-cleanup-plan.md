# Runtime Architecture Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Systematically clean up the runtime/workflow architecture by unifying context handling, removing fake dependency injection, standardizing model fallback diagnostics, and clarifying ownership between app, factory, runner, and runtime layers.

**Architecture:** Implement the cleanup in phases so behavior stays stable while internal structure improves. Phase 1 fixes the highest-risk protocol bugs around `context` handling. Phase 2 removes design inconsistencies in routing and injected callbacks. Phase 3 standardizes LLM access and diagnostics. Phase 4 tightens types and clarifies long-term architecture boundaries.

**Tech Stack:** Python, dataclasses, existing workflow/runtime modules, pytest, typed helper functions / `TypedDict` where useful.

### Phase 1: Unify `context` protocol in model-backed workflow paths

**Status:** Completed

**Files:**
- Modify: `agent_runtime_framework/workflow/goal_analysis.py`
- Modify: `agent_runtime_framework/workflow/decomposition.py`
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Reference: `agent_runtime_framework/workflow/planner_v2.py`
- Reference: `agent_runtime_framework/workflow/llm_synthesis.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write failing tests for dict-based context support**

Add focused tests proving these functions work when `context` is a `dict` containing `application_context`:

```python
def test_analyze_goal_accepts_dict_context_with_application_context(monkeypatch):
    ...

def test_decompose_goal_accepts_dict_context_with_application_context(monkeypatch):
    ...

def test_compile_compat_workflow_graph_accepts_dict_context_with_application_context(monkeypatch):
    ...
```

**Step 2: Run each test to verify correct failure**

Run:
- `pytest tests/test_workflow_graph_builder.py::test_analyze_goal_accepts_dict_context_with_application_context -v`
- `pytest tests/test_workflow_graph_builder.py::test_decompose_goal_accepts_dict_context_with_application_context -v`
- `pytest tests/test_workflow_graph_builder.py::test_compile_compat_workflow_graph_accepts_dict_context_with_application_context -v`

Expected: FAIL because those modules still assume `context.application_context`.

**Step 3: Write minimal implementation**

Create or reuse a shared helper for application-context extraction. Preferred options:
- move the helper to a shared workflow module, or
- reuse `agent_runtime_framework/workflow/llm_synthesis.py:get_application_context`

Update these call sites to use that helper instead of direct attribute access:
- `goal_analysis._analyze_goal_with_model(...)`
- `decomposition._decompose_goal_with_model(...)`
- `graph_builder._build_graph_with_model(...)`

**Step 4: Run focused tests to verify pass**

Run the three tests above again.
Expected: PASS.

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/goal_analysis.py agent_runtime_framework/workflow/decomposition.py agent_runtime_framework/workflow/graph_builder.py tests/test_workflow_graph_builder.py
git commit -m "fix: unify application context extraction in workflow model paths"
```

### Phase 2: Remove fake dependency injection in root routing

**Status:** Completed

**Files:**
- Modify: `agent_runtime_framework/demo/runtime_factory.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Modify: `agent_runtime_framework/workflow/root_graph_runtime.py`
- Test: `tests/test_demo_app.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write failing tests for injected context flow**

Add tests proving that `RootGraphRuntime` actually uses the `context` passed through `analyze_goal_fn` instead of silently routing through `self.app.context`:

```python
def test_root_graph_runtime_passes_injected_context_to_goal_analysis():
    ...


def test_demo_runtime_factory_analyze_goal_fn_uses_runtime_context():
    ...
```

**Step 2: Run tests to verify they fail**

Run:
- `pytest tests/test_demo_app.py::test_root_graph_runtime_passes_injected_context_to_goal_analysis -v`
- `pytest tests/test_demo_app.py::test_demo_runtime_factory_analyze_goal_fn_uses_runtime_context -v`

Expected: FAIL because the factory ignores `_context`.

**Step 3: Write minimal implementation**

Refactor so that:
- `DemoAssistantApp._analyze_workflow_goal(...)` optionally accepts an explicit `context`
- OR `DemoRuntimeFactory.build_root_graph_runtime()` injects `analyze_goal` directly

Preferred end state:
- `RootGraphRuntime` owns routing/orchestration
- `analyze_goal(...)` stays pure
- `mark_route_decision(...)` is the single route-decision side effect

Also remove duplicated `_last_route_decision` writes from app-level analysis if no longer needed.

**Step 4: Run focused tests to verify pass**

Run the two tests above again.
Expected: PASS.

**Step 5: Commit**

```bash
git add agent_runtime_framework/demo/runtime_factory.py agent_runtime_framework/demo/app.py agent_runtime_framework/workflow/root_graph_runtime.py tests/test_demo_app.py
git commit -m "refactor: remove fake goal-analysis injection through app"
```

### Phase 3: Standardize fallback diagnostics across workflow model paths

**Status:** Completed

**Files:**
- Modify: `agent_runtime_framework/workflow/goal_analysis.py`
- Modify: `agent_runtime_framework/workflow/decomposition.py`
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Modify: `agent_runtime_framework/workflow/planner_v2.py`
- Test: `tests/test_workflow_graph_builder.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1: Write failing tests for structured fallback metadata**

Add tests that pin consistent diagnostics for model-vs-fallback behavior. Example expectations:

```python
def test_goal_analysis_records_strategy_and_fallback_reason():
    ...


def test_graph_builder_records_strategy_and_fallback_reason():
    ...


def test_planner_records_model_strategy_on_success_and_fallback_on_failure():
    ...
```

If returning richer metadata from pure functions is too invasive, pin the next-best stable surface such as graph metadata or payload metadata.

**Step 2: Run tests to verify failure**

Run targeted tests for the new diagnostic behavior.
Expected: FAIL until metadata is standardized.

**Step 3: Write minimal implementation**

Define a consistent diagnostic vocabulary, such as:
- `strategy: "model" | "deterministic" | "fallback"`
- `fallback_reason: str | None`
- `model_role: "planner" | ...`

Apply it consistently in:
- goal analysis result metadata
n- decomposition metadata if appropriate
- compiled graph metadata
- planned subgraph metadata

Do not silently swallow failures without any observable trace on the returned artifact.

**Step 4: Run focused tests to verify pass**

Run the new diagnostics tests.
Expected: PASS.

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/goal_analysis.py agent_runtime_framework/workflow/decomposition.py agent_runtime_framework/workflow/graph_builder.py agent_runtime_framework/workflow/planner_v2.py tests/test_workflow_graph_builder.py tests/test_workflow_runtime.py
git commit -m "feat: standardize workflow model fallback diagnostics"
```

### Phase 4: Extract shared workflow LLM-access helper

**Status:** Completed

**Files:**
- Create: `agent_runtime_framework/workflow/llm_access.py`
- Modify: `agent_runtime_framework/workflow/goal_analysis.py`
- Modify: `agent_runtime_framework/workflow/decomposition.py`
- Modify: `agent_runtime_framework/workflow/graph_builder.py`
- Modify: `agent_runtime_framework/workflow/planner_v2.py`
- Modify: `agent_runtime_framework/workflow/llm_synthesis.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write failing tests for shared helper behavior**

Add unit tests around a common helper interface such as:

```python
def test_workflow_llm_access_resolves_application_context_from_dict():
    ...

def test_workflow_llm_access_returns_none_when_model_is_unavailable():
    ...
```

**Step 2: Run tests to verify failure**

Run:
- `pytest tests/test_workflow_graph_builder.py -k "llm_access" -v`

Expected: FAIL because helper does not exist.

**Step 3: Write minimal implementation**

Create `workflow/llm_access.py` with small reusable helpers, for example:
- `get_application_context(context)`
- `resolve_workflow_model_runtime(context, role)`
- `chat_json(context, role, system_prompt, payload, max_tokens)`

Refactor the 4 model-backed workflow modules to reuse it.

**Step 4: Run focused tests to verify pass**

Run the helper-focused tests plus a representative planner/graph-builder subset.
Expected: PASS.

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/llm_access.py agent_runtime_framework/workflow/goal_analysis.py agent_runtime_framework/workflow/decomposition.py agent_runtime_framework/workflow/graph_builder.py agent_runtime_framework/workflow/planner_v2.py agent_runtime_framework/workflow/llm_synthesis.py tests/test_workflow_graph_builder.py
git commit -m "refactor: share workflow llm access helpers"
```

### Phase 5: Reduce factory-layer business logic and anonymous lambdas

**Status:** Completed

**Files:**
- Modify: `agent_runtime_framework/demo/runtime_factory.py`
- Modify: `agent_runtime_framework/demo/agent_branch_runner.py`
- Modify: `agent_runtime_framework/demo/compat_workflow_runner.py`
- Modify: `agent_runtime_framework/demo/run_lifecycle_service.py`
- Test: `tests/test_demo_app.py`

**Step 1: Write failing tests for named orchestration helpers**

Pin desired behavior without depending on anonymous lambda wiring. Example targets:

```python
def test_runtime_factory_builds_root_runtime_with_named_services():
    ...
```

**Step 2: Run tests to verify failure**

Run targeted tests for runtime-factory composition.
Expected: FAIL if the test references helpers that do not yet exist.

**Step 3: Write minimal implementation**

Move inline lambdas that carry business meaning into named private methods or small service objects, such as:
- `_analyze_goal(...)`
- `_run_conversation_branch(...)`
- `_run_agent_branch(...)`
- `_record_run(...)`

Keep `DemoRuntimeFactory` focused on wiring rather than hidden orchestration.

**Step 4: Run focused tests to verify pass**

Run the factory composition tests.
Expected: PASS.

**Step 5: Commit**

```bash
git add agent_runtime_framework/demo/runtime_factory.py agent_runtime_framework/demo/agent_branch_runner.py agent_runtime_framework/demo/compat_workflow_runner.py agent_runtime_framework/demo/run_lifecycle_service.py tests/test_demo_app.py
git commit -m "refactor: reduce factory-layer lambda orchestration"
```

### Phase 6: Tighten key boundary types and payload contracts

**Status:** Completed

**Files:**
- Modify: `agent_runtime_framework/workflow/root_graph_runtime.py`
- Modify: `agent_runtime_framework/demo/agent_branch_runner.py`
- Modify: `agent_runtime_framework/demo/compat_workflow_runner.py`
- Modify: `agent_runtime_framework/demo/app.py`
- Create or Modify: typed payload module as needed
- Test: `tests/test_demo_app.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1: Write failing tests for typed boundary contracts**

Introduce tests around stable payload keys and shapes if needed before adding `TypedDict` or dataclasses.

**Step 2: Run tests to verify failure**

Run the new payload/contract tests.
Expected: FAIL if the types/helpers do not exist.

**Step 3: Write minimal implementation**

Replace the highest-value `Any` / `dict[str, Any]` boundaries with stronger contracts, such as:
- `RootGraphPayload`
- `PlannerDiagnostics`
- `RouteDecisionPayload`

Use `TypedDict` first unless a dataclass clearly improves behavior.

**Step 4: Run focused tests to verify pass**

Run targeted payload-contract tests.
Expected: PASS.

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/root_graph_runtime.py agent_runtime_framework/demo/agent_branch_runner.py agent_runtime_framework/demo/compat_workflow_runner.py agent_runtime_framework/demo/app.py tests/test_demo_app.py tests/test_workflow_runtime.py
git commit -m "refactor: tighten runtime payload boundary types"
```

### Phase 7: Clarify long-term architecture and compat boundaries

**Status:** Completed

**Files:**
- Modify: `docs/plans/2026-04-01-agent-graph-runtime-design.md`
- Modify: `docs/plans/2026-04-01-agent-architecture-migration-plan.md`
- Create: optional architecture note if needed
- Reference: `agent_runtime_framework/demo/compat_workflow_runner.py`
- Reference: `agent_runtime_framework/workflow/root_graph_runtime.py`

**Step 1: Document current-vs-target ownership**

Add a concise architecture note covering:
- `App` responsibilities
- `Factory` responsibilities
- `Runner` responsibilities
- `Runtime` responsibilities
- which modules are transitional compatibility layers

**Step 2: Review docs against code**

Read the implementation and update terminology to match current code.

**Step 3: Write minimal documentation update**

Document:
- target architecture
- migration status
- remaining compatibility seams
- design rules for future contributors

**Step 4: Verify docs are internally consistent**

Run a quick grep or manual read for terminology consistency.
Expected: no contradictory ownership claims.

**Step 5: Commit**

```bash
git add docs/plans/2026-04-01-agent-graph-runtime-design.md docs/plans/2026-04-01-agent-architecture-migration-plan.md
git commit -m "docs: clarify runtime architecture ownership and compat boundaries"
```

### Final Verification Pass

**Files:**
- Reference: `tests/test_workflow_graph_builder.py`
- Reference: `tests/test_workflow_runtime.py`
- Reference: `tests/test_demo_app.py`

**Step 1: Run workflow-focused regression tests**

Run:
- `pytest tests/test_workflow_graph_builder.py -v`
- `pytest tests/test_workflow_runtime.py -v`

Expected: PASS.

**Step 2: Run app/demo-focused regression tests**

Run:
- `pytest tests/test_demo_app.py -v`

Expected: PASS.

**Step 3: Review final diff scope**

Run:
- `git diff -- agent_runtime_framework/demo agent_runtime_framework/workflow tests docs/plans`

Expected: only context cleanup, runtime/factory cleanup, diagnostics standardization, typing, and docs.

**Step 4: Sanity-check architecture invariants**

Confirm manually:
- model-backed workflow modules share one context-access pattern
- `RootGraphRuntime` does not depend on app-specific analysis side effects
- fallback diagnostics are visible and consistent
- compatibility seams remain explicit

**Step 5: Commit**

```bash
git add agent_runtime_framework/demo agent_runtime_framework/workflow tests docs/plans
git commit -m "refactor: clean up runtime architecture boundaries"
```
