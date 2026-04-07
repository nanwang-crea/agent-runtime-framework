# Model Judge Routing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current hard-rule workflow judge with a model-driven routing controller that outputs route constraints for planner consumption.

**Architecture:** Keep the current `judge -> planner -> subgraph` flow, but change the judge contract from rule-style statuses into a model-generated route contract. The runtime should only recognize `accept` and `replan`; clarification becomes a normal planner-selected node under `replan`, and planner must honor the judge's allowed and blocked node constraints.

**Tech Stack:** Python 3.12, pytest, existing workflow runtime, model-backed planner/judge pipeline

### Task 1: Define the new judge route contract

**Files:**
- Modify: `agent_runtime_framework/workflow/models.py`
- Modify: `agent_runtime_framework/workflow/agent_graph_state_store.py`
- Test: `tests/test_workflow_models.py`

**Step 1: Write the failing test**

```python
def test_judge_decision_serializes_route_constraints():
    decision = JudgeDecision(
        status="replan",
        reason="Need grounded README content",
        allowed_next_node_types=["plan_read", "chunked_file_read"],
        blocked_node_types=["final_response"],
        must_cover=["read README body"],
        planner_instructions="Read the file before answering.",
    )

    payload = decision.as_payload()

    assert payload["status"] == "replan"
    assert payload["allowed_next_node_types"] == ["plan_read", "chunked_file_read"]
    assert payload["blocked_next_node_types"] == ["final_response"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_models.py -k judge_decision_serializes_route_constraints -v`
Expected: FAIL because `JudgeDecision` does not expose route-constraint fields yet.

**Step 3: Write minimal implementation**

Add route-constraint fields to `JudgeDecision`, keep payload serialization stable, and update state restore/serialization to preserve the new fields.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_models.py -k judge_decision_serializes_route_constraints -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/models.py agent_runtime_framework/workflow/agent_graph_state_store.py tests/test_workflow_models.py docs/plans/2026-04-07-model-judge-routing-plan.md
git commit -m "refactor: define model judge route contract"
```

### Task 2: Add failing workflow tests for route-driven replanning

**Files:**
- Modify: `tests/test_workflow_runtime.py`
- Modify: `tests/test_workflow_decomposition.py`

**Step 1: Write the failing tests**

```python
def test_judge_progress_file_read_requires_read_grounding():
    ...
    assert decision.status == "replan"
    assert "chunked_file_read" in decision.allowed_next_node_types
    assert "final_response" in decision.blocked_next_node_types
```

```python
def test_agent_graph_runtime_replans_without_special_clarification_branch():
    ...
    assert result.status == RUN_STATUS_COMPLETED
    assert result.metadata["agent_graph_state"]["judge_history"][0]["status"] == "replan"
```

```python
def test_planner_prompt_receives_latest_judge_route_constraints():
    ...
    assert '"allowed_next_node_types"' in request_body
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_decomposition.py -k "requires_read_grounding or replans_without_special_clarification_branch or latest_judge_route_constraints" -v`
Expected: FAIL because current judge and runtime still use old hard statuses and special-case clarification handling.

**Step 3: Write minimal implementation**

Only add the assertions and fixtures needed to lock the new behavior before touching production code.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_decomposition.py -k "requires_read_grounding or replans_without_special_clarification_branch or latest_judge_route_constraints" -v`
Expected: PASS after Tasks 3 and 4 are implemented.

**Step 5: Commit**

```bash
git add tests/test_workflow_runtime.py tests/test_workflow_decomposition.py
git commit -m "test: cover model judge route-driven replanning"
```

### Task 3: Replace hard-rule judge logic with model-driven routing

**Files:**
- Modify: `agent_runtime_framework/workflow/judge.py`
- Modify: `agent_runtime_framework/workflow/memory_views.py`
- Modify: `agent_runtime_framework/workflow/llm_access.py` if needed
- Test: `tests/test_workflow_runtime.py`

**Step 1: Write the failing test**

```python
def test_judge_progress_uses_model_route_contract_when_available(monkeypatch):
    ...
    assert decision.status == "replan"
    assert decision.allowed_next_node_types == ["plan_read", "chunked_file_read"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_runtime.py -k uses_model_route_contract_when_available -v`
Expected: FAIL because `judge_progress()` is still rule-driven and does not call the model.

**Step 3: Write minimal implementation**

Refactor `judge_progress()` to:
- build a compact judge context payload
- call the judge model
- normalize model output into the new `JudgeDecision`
- keep only minimal field-level guards such as empty/invalid outputs and iteration budget handoff

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_runtime.py -k uses_model_route_contract_when_available -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/judge.py agent_runtime_framework/workflow/memory_views.py tests/test_workflow_runtime.py
git commit -m "refactor: drive workflow routing through model judge"
```

### Task 4: Make planner and runtime consume judge route constraints

**Files:**
- Modify: `agent_runtime_framework/workflow/subgraph_planner.py`
- Modify: `agent_runtime_framework/workflow/planner_prompts.py`
- Modify: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Modify: `agent_runtime_framework/workflow/node_executors.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_decomposition.py`

**Step 1: Write the failing test**

```python
def test_model_planner_rejects_nodes_blocked_by_judge_contract():
    ...
    with pytest.raises(ValueError):
        plan_next_subgraph(...)
```

```python
def test_final_response_executor_only_runs_after_accept():
    ...
    assert result.status == NODE_STATUS_FAILED
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_decomposition.py -k "blocked_by_judge_contract or only_runs_after_accept" -v`
Expected: FAIL because planner does not enforce judge route constraints yet.

**Step 3: Write minimal implementation**

Update planner and runtime so that:
- planner receives latest judge contract
- planned nodes must stay within `allowed_next_node_types`
- blocked nodes cause planner normalization failure
- runtime treats any non-`accept` decision as another planning loop, without a special clarification branch

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_runtime.py tests/test_workflow_decomposition.py -k "blocked_by_judge_contract or only_runs_after_accept" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/subgraph_planner.py agent_runtime_framework/workflow/planner_prompts.py agent_runtime_framework/workflow/agent_graph_runtime.py agent_runtime_framework/workflow/node_executors.py tests/test_workflow_runtime.py tests/test_workflow_decomposition.py
git commit -m "refactor: enforce judge route constraints in planner and runtime"
```

### Task 5: Verify end-to-end behavior and clean up compatibility edges

**Files:**
- Modify: `agent_runtime_framework/demo/workflow_payload_builder.py`
- Modify: `tests/test_workflow_continuation.py`
- Modify: `tests/test_workflow_persistence.py`

**Step 1: Write the failing test**

```python
def test_continuation_payload_keeps_replan_route_contract():
    ...
    assert payload["judge"]["status"] == "replan"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_continuation.py tests/test_workflow_persistence.py -k "replan_route_contract" -v`
Expected: FAIL because downstream payload builders and persisted history still assume old statuses.

**Step 3: Write minimal implementation**

Normalize downstream payload builders and state restore code to preserve the new route contract shape without reintroducing old hard-status semantics.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_continuation.py tests/test_workflow_persistence.py -k "replan_route_contract" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/demo/workflow_payload_builder.py tests/test_workflow_continuation.py tests/test_workflow_persistence.py
git commit -m "test: preserve model judge route contracts across continuation flows"
```
