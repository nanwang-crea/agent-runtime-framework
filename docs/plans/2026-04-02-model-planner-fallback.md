# Model Planner Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a model-first subgraph planner that falls back to the current deterministic planner whenever model planning is unavailable, invalid, or unsafe.

**Architecture:** Keep `plan_next_subgraph()` as the public planner entrypoint, but split today’s logic into a deterministic fallback helper and a model planner helper. The model planner returns a JSON subgraph draft, a local validator normalizes and rejects unsafe output, and any failure path falls back to the existing deterministic planner without breaking current behavior.

**Tech Stack:** Python, dataclasses, existing workflow models, existing model runtime helpers, pytest.

### Task 1: Lock current deterministic behavior with tests

**Files:**
- Modify: `tests/test_workflow_graph_builder.py`
- Reference: `agent_runtime_framework/workflow/planner_v2.py`

**Step 1: Write the failing test**

Add tests that pin deterministic fallback behavior behind a helper that will remain after refactor:

```python
def test_deterministic_planner_builds_file_read_subgraph():
    from agent_runtime_framework.workflow.planner_v2 import _plan_next_subgraph_deterministically

    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
        success_criteria=["collect README evidence"],
    )
    state = new_agent_graph_state(run_id="run-det-1", goal_envelope=goal)

    subgraph = _plan_next_subgraph_deterministically(goal, state, context=None)

    assert [node.node_type for node in subgraph.nodes] == [
        "content_search",
        "chunked_file_read",
        "evidence_synthesis",
    ]
    assert subgraph.metadata["planner"] == "deterministic_v2"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_graph_builder.py::test_deterministic_planner_builds_file_read_subgraph -v`
Expected: FAIL with import error or missing helper.

**Step 3: Write minimal implementation**

Extract the current deterministic body of `plan_next_subgraph()` into `_plan_next_subgraph_deterministically(...)` and keep behavior identical.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_graph_builder.py::test_deterministic_planner_builds_file_read_subgraph -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_workflow_graph_builder.py agent_runtime_framework/workflow/planner_v2.py
git commit -m "refactor: extract deterministic planner helper"
```

### Task 2: Add model planner draft + parser

**Files:**
- Modify: `agent_runtime_framework/workflow/planner_v2.py`
- Reference: `agent_runtime_framework/workflow/goal_analysis.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing tests**

Add tests for a model planner helper that accepts a mocked model response and returns a validated draft:

```python
def test_model_planner_uses_valid_json_draft(monkeypatch):
    from agent_runtime_framework.workflow import planner_v2

    goal = GoalEnvelope(
        goal="解释 README.md",
        normalized_goal="解释 README.md",
        intent="target_explainer",
        target_hints=["README.md"],
        success_criteria=["produce a grounded response"],
    )
    state = new_agent_graph_state(run_id="run-model-1", goal_envelope=goal)

    monkeypatch.setattr(planner_v2, "_call_model_planner", lambda *args, **kwargs: {
        "nodes": [
            {"node_id": "resolve", "node_type": "target_resolution", "reason": "Resolve target", "inputs": {"query": "解释 README.md"}, "depends_on": [], "success_criteria": ["resolve target"]},
            {"node_id": "read", "node_type": "chunked_file_read", "reason": "Read target", "inputs": {"target_path": "README.md"}, "depends_on": ["resolve"], "success_criteria": ["read target"]},
        ],
        "planner_summary": "Model plan for target_explainer",
    })

    subgraph = planner_v2._plan_next_subgraph_with_model(goal, state, context=None)

    assert [node.node_type for node in subgraph.nodes] == ["target_resolution", "chunked_file_read"]
    assert subgraph.metadata["planner"] == "model_v1"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_graph_builder.py::test_model_planner_uses_valid_json_draft -v`
Expected: FAIL with missing helper.

**Step 3: Write minimal implementation**

Add these helpers in `agent_runtime_framework/workflow/planner_v2.py`:
- `_call_model_planner(...)`
- `_plan_next_subgraph_with_model(...)`
- `_normalize_model_planned_nodes(...)`

Use the same model access pattern already used in `agent_runtime_framework/workflow/goal_analysis.py`:
- `resolve_model_runtime(...)`
- `chat_once(...)`
- `extract_json_block(...)`

Prompt the model to return JSON only with:
- `planner_summary`
- `nodes[]` containing `node_id`, `node_type`, `reason`, `inputs`, `depends_on`, `success_criteria`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_graph_builder.py::test_model_planner_uses_valid_json_draft -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_workflow_graph_builder.py agent_runtime_framework/workflow/planner_v2.py
git commit -m "feat: add model planner draft helper"
```

### Task 3: Add local validation and hard fallback

**Files:**
- Modify: `agent_runtime_framework/workflow/planner_v2.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing tests**

Add fallback coverage for invalid model output:

```python
def test_plan_next_subgraph_falls_back_when_model_returns_invalid_node_type(monkeypatch):
    from agent_runtime_framework.workflow import planner_v2

    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
        success_criteria=["collect README evidence"],
    )
    state = new_agent_graph_state(run_id="run-fallback-1", goal_envelope=goal)

    monkeypatch.setattr(planner_v2, "_plan_next_subgraph_with_model", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad node")))

    subgraph = planner_v2.plan_next_subgraph(goal, state, context=None)

    assert [node.node_type for node in subgraph.nodes] == [
        "content_search",
        "chunked_file_read",
        "evidence_synthesis",
    ]
    assert subgraph.metadata["planner"] == "deterministic_v2"
    assert subgraph.metadata["fallback_reason"] == "bad node"
```

Also add one test for model-unavailable fallback.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_workflow_graph_builder.py -k "planner_v2 or fallback" -v`
Expected: FAIL because fallback orchestration is not implemented.

**Step 3: Write minimal implementation**

Update `plan_next_subgraph(...)` to:
- try model planning first
- validate every node type against `ALLOWED_DYNAMIC_NODE_TYPES`
- enforce `max_dynamic_nodes`
- reject empty node lists
- reject dependencies that reference unknown nodes
- reject duplicate node ids
- on any exception, call `_plan_next_subgraph_deterministically(...)`
- include metadata such as:
  - `planner: "model_v1"` on success
  - `planner: "deterministic_v2"` and `fallback_reason` on fallback

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_workflow_graph_builder.py -k "planner_v2 or fallback" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_workflow_graph_builder.py agent_runtime_framework/workflow/planner_v2.py
git commit -m "feat: add planner fallback for invalid model plans"
```

### Task 4: Add configuration gate and safe defaults

**Files:**
- Modify: `agent_runtime_framework/workflow/planner_v2.py`
- Modify: `agent_runtime_framework/workflow/goal_intake.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing tests**

Add tests for planner mode selection:

```python
def test_plan_next_subgraph_skips_model_when_config_disables_it():
    goal = GoalEnvelope(
        goal="读取 README.md",
        normalized_goal="读取 README.md",
        intent="file_read",
        target_hints=["README.md"],
        constraints={"planner_mode": "deterministic"},
        success_criteria=["collect README evidence"],
    )
    state = new_agent_graph_state(run_id="run-gated-1", goal_envelope=goal)

    subgraph = plan_next_subgraph(goal, state, context=None)

    assert subgraph.metadata["planner"] == "deterministic_v2"
    assert "fallback_reason" not in subgraph.metadata
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_graph_builder.py::test_plan_next_subgraph_skips_model_when_config_disables_it -v`
Expected: FAIL because planner mode is not supported.

**Step 3: Write minimal implementation**

Add `_planner_mode(goal_envelope, context)` with modes:
- `model_with_fallback` default
- `deterministic`

Expose optional config plumbing from `goal_intake.py` into `GoalEnvelope.constraints` if application config contains planner settings.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_workflow_graph_builder.py::test_plan_next_subgraph_skips_model_when_config_disables_it -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_workflow_graph_builder.py agent_runtime_framework/workflow/planner_v2.py agent_runtime_framework/workflow/goal_intake.py
git commit -m "feat: add planner mode configuration"
```

### Task 5: Verify runtime integration stays stable

**Files:**
- Reference: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Test: `tests/test_workflow_runtime.py`
- Test: `tests/test_workflow_graph_builder.py`

**Step 1: Write the failing integration test**

Add an `AgentGraphRuntime` test that monkeypatches the planner module to force model failure and confirms the runtime still completes using fallback subgraphs.

```python
def test_agent_graph_runtime_survives_model_planner_failure(monkeypatch):
    from agent_runtime_framework.workflow import planner_v2
    from agent_runtime_framework.workflow.agent_graph_runtime import AgentGraphRuntime

    monkeypatch.setattr(planner_v2, "_plan_next_subgraph_with_model", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("planner offline")))

    runtime = AgentGraphRuntime(workflow_runtime=_stub_workflow_runtime())
    run = runtime.run(_goal_envelope("读取 README.md"), context={})

    assert run.status in {"completed", "running", "waiting_approval"}
    assert run.metadata["agent_graph_state"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_runtime.py::test_agent_graph_runtime_survives_model_planner_failure -v`
Expected: FAIL until fallback metadata and planner path are wired.

**Step 3: Write minimal implementation**

Adjust only what is needed so runtime integration preserves current semantics. Do not change `AgentGraphRuntime` public API.

**Step 4: Run focused verification**

Run: `pytest tests/test_workflow_graph_builder.py -k planner -v`
Expected: PASS.

Run: `pytest tests/test_workflow_runtime.py::test_agent_graph_runtime_survives_model_planner_failure -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_workflow_graph_builder.py tests/test_workflow_runtime.py agent_runtime_framework/workflow/planner_v2.py agent_runtime_framework/workflow/goal_intake.py
git commit -m "test: verify planner fallback through runtime"
```

### Task 6: Final regression pass

**Files:**
- Reference: `agent_runtime_framework/workflow/planner_v2.py`
- Reference: `tests/test_workflow_graph_builder.py`
- Reference: `tests/test_workflow_runtime.py`

**Step 1: Run planner-focused test suite**

Run: `pytest tests/test_workflow_graph_builder.py -k planner -v`
Expected: PASS.

**Step 2: Run runtime-focused test suite**

Run: `pytest tests/test_workflow_runtime.py -k agent_graph_runtime -v`
Expected: PASS.

**Step 3: Run targeted full regression for workflow area**

Run: `pytest tests/test_workflow_graph_builder.py tests/test_workflow_runtime.py -v`
Expected: PASS or only unrelated pre-existing failures.

**Step 4: Review diffs**

Run: `git diff -- agent_runtime_framework/workflow/planner_v2.py agent_runtime_framework/workflow/goal_intake.py tests/test_workflow_graph_builder.py tests/test_workflow_runtime.py`
Expected: only planner fallback and tests.

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/planner_v2.py agent_runtime_framework/workflow/goal_intake.py tests/test_workflow_graph_builder.py tests/test_workflow_runtime.py
git commit -m "feat: add model-first planner with deterministic fallback"
```

