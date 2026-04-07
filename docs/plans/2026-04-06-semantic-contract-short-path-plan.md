# Semantic Contract Short Path Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Tighten the workflow into a four-layer chain where intent only routes, semantic nodes produce strict contracts, tools only consume those contracts, and judge blocks path drift for simple read tasks.

**Architecture:** Keep the current agent graph runtime, but make each layer narrower and more deterministic. First enforce strict semantic-plan schemas and judge path consistency, then add a constrained planner mode for confirmed file reads, then remove tool-layer guessing so execution must follow the semantic plan.

**Tech Stack:** Python, pytest, workflow runtime, semantic planner executors, agent graph planner/judge

### Task 1: Tighten semantic plan contracts and goal-analysis outputs

Status: Completed

**Files:**
- Modify: `agent_runtime_framework/workflow/models.py`
- Modify: `agent_runtime_framework/workflow/goal_analysis.py`
- Modify: `agent_runtime_framework/workflow/semantic_plan_executors.py`
- Test: `tests/test_workflow_decomposition.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1: Write the failing tests**

Status: Completed

Add tests that assert:

```python
def test_analyze_goal_returns_only_routing_flags():
    goal = analyze_goal("读取 README.md", context=context)
    assert goal.primary_intent == "file_read"
    assert goal.requires_target_interpretation is True
    assert goal.requires_search is False
    assert goal.requires_read is True
    assert goal.requires_verification is False


def test_interpret_target_requires_confirmed_and_preferred_path(monkeypatch):
    monkeypatch.setattr(
        "agent_runtime_framework.workflow.semantic_plan_executors._structured_semantic_plan",
        lambda *args, **kwargs: {
            "target_kind": "file",
            "scope_preference": "workspace_root",
            "exclude_paths": [],
            "confidence": 0.9,
        },
    )
    with pytest.raises(ValueError):
        InterpretTargetExecutor().execute(node, run, context={})
```

Add matching tests for `plan_search` missing `semantic_queries` and `plan_read` missing `target_path`.

**Step 2: Run tests to verify they fail**

Status: Completed

Run: `pytest tests/test_workflow_decomposition.py tests/test_workflow_runtime.py -k "analyze_goal_returns_only_routing_flags or requires_confirmed or missing semantic_queries or missing target_path" -v`

Expected: FAIL because `GoalSpec` still exposes old fields and semantic planning still falls back to defaults.

**Step 3: Write minimal implementation**

Status: Completed

Change `GoalSpec` to carry:

```python
@dataclass(slots=True)
class GoalSpec:
    original_goal: str
    primary_intent: str
    requires_target_interpretation: bool = False
    requires_search: bool = False
    requires_read: bool = False
    requires_verification: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

Update `goal_analysis.py` to read those fields from model output and stop emitting `target_paths`.

Update semantic normalizers so they require:

```python
{
    "target_kind": str,
    "preferred_path": str,
    "scope_preference": str,
    "exclude_paths": list[str],
    "confirmed": bool,
    "confidence": float,
}
```

```python
{
    "search_goal": str,
    "semantic_queries": list[str],
    "path_bias": list[str],
    "must_avoid": list[str],
}
```

```python
{
    "read_goal": str,
    "target_path": str,
    "preferred_regions": list[str],
}
```

Reject missing required fields instead of silently filling them from `run.goal`, hints, or other plans.

**Step 4: Run tests to verify they pass**

Status: Completed

Run: `pytest tests/test_workflow_decomposition.py tests/test_workflow_runtime.py -k "analyze_goal_returns_only_routing_flags or requires_confirmed or missing semantic_queries or missing target_path" -v`

Expected: PASS

**Step 5: Commit**

Status: Completed

```bash
git add agent_runtime_framework/workflow/models.py agent_runtime_framework/workflow/goal_analysis.py agent_runtime_framework/workflow/semantic_plan_executors.py tests/test_workflow_decomposition.py tests/test_workflow_runtime.py
git commit -m "refactor: tighten semantic planning contracts"
```

### Task 2: Add judge path-consistency checks

Status: Completed

**Files:**
- Modify: `agent_runtime_framework/workflow/judge.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1: Write the failing tests**

Status: Completed

Add tests that assert:

```python
def test_judge_progress_rejects_evidence_when_confirmed_target_differs():
    state = new_agent_graph_state(run_id="judge-1", goal_envelope=goal)
    state.memory_state.semantic_memory = {
        "confirmed_targets": ["README.md"],
        "read_plan": {"target_path": "README.md"},
    }
    decision = judge_progress(
        goal,
        normalize_aggregated_workflow_payload({
            "evidence_items": [{"path": "docs/README.md"}],
            "chunks": [],
            "facts": [],
        }),
        state,
    )
    assert decision.status == "needs_more_evidence"
```

Add companion tests for excluded-target hits and mismatched `read_plan.target_path`.

**Step 2: Run tests to verify they fail**

Status: Completed

Run: `pytest tests/test_workflow_runtime.py -k "confirmed_target_differs or excluded_target or read_plan_target_path" -v`

Expected: FAIL because judge currently only checks generic conflicts.

**Step 3: Write minimal implementation**

Status: Completed

Add a helper in `judge.py` that compares normalized evidence/read paths against:
- `confirmed_targets`
- `excluded_targets`
- `semantic_constraints.read_plan.target_path`

Convert any mismatch into `conflicts`, and let the existing conflict branch reject the iteration.

**Step 4: Run tests to verify they pass**

Status: Completed

Run: `pytest tests/test_workflow_runtime.py -k "confirmed_target_differs or excluded_target or read_plan_target_path" -v`

Expected: PASS

**Step 5: Commit**

Status: Completed

```bash
git add agent_runtime_framework/workflow/judge.py tests/test_workflow_runtime.py
git commit -m "feat: add semantic path consistency checks to judge"
```

### Task 3: Constrain planner after clarification or target resolution

Status: Completed

**Files:**
- Modify: `agent_runtime_framework/workflow/subgraph_planner.py`
- Modify: `agent_runtime_framework/workflow/clarification_interpreter.py`
- Modify: `agent_runtime_framework/demo/agent_branch_orchestrator.py`
- Test: `tests/test_workflow_decomposition.py`
- Test: `tests/test_workflow_continuation.py`

**Step 1: Write the failing tests**

Status: Completed

Add tests that assert:

```python
def test_plan_next_subgraph_uses_constrained_read_path_for_confirmed_file_read():
    state.memory_state.semantic_memory = {
        "confirmed_targets": ["README.md"],
        "interpreted_target": {"confirmed": True, "preferred_path": "README.md"},
    }
    subgraph = plan_next_subgraph(envelope, state, context=context)
    assert [node.node_type for node in subgraph.nodes] == ["plan_read", "chunked_file_read", "final_response"]
```

```python
def test_agent_branch_orchestrator_marks_confirmed_target_after_unique_clarification():
    assert call["clarification_resolution"]["confirmed_target"] == "README.md"
    assert call["prior_state"]["memory_state"]["semantic_memory"]["confirmed_targets"] == ["README.md"]
```

**Step 2: Run tests to verify they fail**

Status: Completed

Run: `pytest tests/test_workflow_decomposition.py tests/test_workflow_continuation.py -k "constrained_read_path or confirmed_target_after_unique_clarification" -v`

Expected: FAIL because planner still relies entirely on model output and clarification does not force execution mode.

**Step 3: Write minimal implementation**

Status: Completed

Add deterministic planner gating:
- If `intent == "file_read"`
- And interpreted target is confirmed or resolved target status is `resolved`
- Then return a fixed subgraph with:
  - `plan_read`
  - `chunked_file_read`
  - `final_response`

Update clarification resolution/orchestration so a unique clarified file writes:
- `confirmed_targets`
- `excluded_targets`
- `interpreted_target.confirmed = True` when enough information is available

Do not generate another clarification node once the target is uniquely confirmed.

**Step 4: Run tests to verify they pass**

Status: Completed

Run: `pytest tests/test_workflow_decomposition.py tests/test_workflow_continuation.py -k "constrained_read_path or confirmed_target_after_unique_clarification" -v`

Expected: PASS

**Step 5: Commit**

Status: Completed

```bash
git add agent_runtime_framework/workflow/subgraph_planner.py agent_runtime_framework/workflow/clarification_interpreter.py agent_runtime_framework/demo/agent_branch_orchestrator.py tests/test_workflow_decomposition.py tests/test_workflow_continuation.py
git commit -m "feat: constrain confirmed file reads to short execution path"
```

### Task 4: Make tool executors consume only structured plans

Status: Completed

**Files:**
- Modify: `agent_runtime_framework/workflow/target_resolution_executor.py`
- Modify: `agent_runtime_framework/workflow/content_search_executor.py`
- Modify: `agent_runtime_framework/workflow/chunked_file_read_executor.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1: Write the failing tests**

Status: Completed

Add tests that assert:

```python
def test_target_resolution_executor_fails_without_interpreted_target():
    result = TargetResolutionExecutor().execute(node, WorkflowRun(goal="看 README"), context=context)
    assert result.status == NODE_STATUS_FAILED
```

```python
def test_content_search_executor_fails_without_search_plan(monkeypatch, tmp_path):
    result = ContentSearchExecutor().execute(node, WorkflowRun(goal="随便一句话", shared_state={"node_results": {}}), context={"workspace_root": str(tmp_path)})
    assert result.status == NODE_STATUS_FAILED
```

```python
def test_chunked_file_read_executor_fails_without_read_plan(monkeypatch, tmp_path):
    result = ChunkedFileReadExecutor().execute(node, WorkflowRun(goal="看 README", shared_state={"node_results": {}}), context={"workspace_root": str(tmp_path)})
    assert result.status == NODE_STATUS_FAILED
```

**Step 2: Run tests to verify they fail**

Status: Completed

Run: `pytest tests/test_workflow_runtime.py -k "fails_without_interpreted_target or fails_without_search_plan or fails_without_read_plan" -v`

Expected: FAIL because executors still infer data from goal text, node metadata, and prior search results.

**Step 3: Write minimal implementation**

Status: Completed

Require:
- `target_resolution` -> `run.shared_state["interpreted_target"]`
- `content_search` -> `run.shared_state["search_plan"]`
- `chunked_file_read` -> `run.shared_state["read_plan"]`

If any required plan is missing, return `NODE_STATUS_FAILED`.

Remove fallback logic that reconstructs search terms or target paths from:
- `run.goal`
- `node.metadata`
- ranked search hits

Keep only deterministic execution derived from the structured plan.

**Step 4: Run tests to verify they pass**

Status: Completed

Run: `pytest tests/test_workflow_runtime.py -k "fails_without_interpreted_target or fails_without_search_plan or fails_without_read_plan or uses_search_plan_queries or uses_read_plan_target_and_region or prefers_interpreted_target_constraints" -v`

Expected: PASS

**Step 5: Commit**

Status: Completed

```bash
git add agent_runtime_framework/workflow/target_resolution_executor.py agent_runtime_framework/workflow/content_search_executor.py agent_runtime_framework/workflow/chunked_file_read_executor.py tests/test_workflow_runtime.py
git commit -m "refactor: make workflow tools consume semantic plans only"
```

### Task 5: End-to-end regression coverage for the short path

Status: Completed

**Files:**
- Modify: `tests/test_workflow_runtime.py`
- Modify: `tests/test_workflow_continuation.py`

**Step 1: Write the failing tests**

Status: Completed

Add end-to-end tests that assert:

```python
def test_simple_confirmed_read_does_not_reenter_search_or_clarification():
    assert runtime.workflow_runtime.calls[-1] == ["plan_read", "chunked_file_read", "final_response"]
```

```python
def test_clarification_to_unique_file_flows_into_read_and_judge_acceptance():
    assert result.status == RUN_STATUS_COMPLETED
    assert result.final_output is not None
```

**Step 2: Run tests to verify they fail**

Status: Completed

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_continuation.py -k "confirmed_read_does_not_reenter_search_or_clarification or clarification_to_unique_file" -v`

Expected: FAIL because the current chain still allows search/clarification drift.

**Step 3: Write minimal implementation**

Status: Completed

Only add the glue needed to make the new contracts work together. Do not add new abstractions unless required by repeated logic.

**Step 4: Run tests to verify they pass**

Status: Completed

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_continuation.py -k "confirmed_read_does_not_reenter_search_or_clarification or clarification_to_unique_file" -v`

Expected: PASS

**Step 5: Commit**

Status: Completed

```bash
git add tests/test_workflow_runtime.py tests/test_workflow_continuation.py
git commit -m "test: cover constrained short path for confirmed file reads"
```
