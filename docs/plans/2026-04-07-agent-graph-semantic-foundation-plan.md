# Agent Graph Semantic Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Promote `interpret_target`, `plan_search`, and `plan_read` into the default semantic foundation of the agent graph so execution nodes no longer run before intent and target semantics are prepared.

**Architecture:** Keep the existing root routing and agent graph runtime, but treat semantic planning as a fixed graph layer rather than optional planner output. The planner should normalize every subgraph so search, read, and target-resolution work always flow through the semantic foundation before execution nodes, and execution nodes should rely on those prepared contracts instead of crashing on missing semantic state.

**Tech Stack:** Python 3.12, pytest, workflow runtime, semantic planning executors, model-backed planner/judge

### Task 1: Lock the semantic foundation behavior with tests

**Files:**
- Modify: `tests/test_workflow_decomposition.py`
- Modify: `tests/test_workflow_runtime.py`

**Step 1: Write the failing test**

```python
def test_plan_next_subgraph_prepends_semantic_foundation_before_search():
    ...
    assert [node.node_type for node in subgraph.nodes[:3]] == [
        "interpret_target",
        "plan_search",
        "content_search",
    ]
```

```python
def test_plan_next_subgraph_prepends_semantic_foundation_before_read():
    ...
    assert [node.node_type for node in subgraph.nodes[:4]] == [
        "interpret_target",
        "plan_search",
        "plan_read",
        "chunked_file_read",
    ]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_decomposition.py -k "semantic_foundation_before_search or semantic_foundation_before_read" -v`
Expected: FAIL because planner currently accepts direct execution nodes without first inserting the semantic chain.

**Step 3: Write minimal implementation**

Add planner normalization that detects when a subgraph includes target-resolution/search/read nodes but lacks the prerequisite semantic nodes in state or in the subgraph itself, then prepend the missing semantic foundation nodes in dependency order.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_decomposition.py -k "semantic_foundation_before_search or semantic_foundation_before_read" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_workflow_decomposition.py
git commit -m "test: require semantic foundation before execution nodes"
```

### Task 2: Move semantic preparation into the graph foundation

**Files:**
- Modify: `agent_runtime_framework/workflow/subgraph_planner.py`
- Modify: `agent_runtime_framework/workflow/planner_prompts.py`
- Test: `tests/test_workflow_decomposition.py`

**Step 1: Write the failing test**

```python
def test_plan_next_subgraph_allows_semantic_prerequisites_even_when_judge_blocks_execution_node():
    ...
    assert subgraph.nodes[0].node_type == "interpret_target"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_decomposition.py -k semantic_prerequisites_even_when_judge_blocks_execution_node -v`
Expected: FAIL because judge route enforcement currently treats all nodes equally and does not understand prerequisite semantic nodes.

**Step 3: Write minimal implementation**

Teach the planner to treat semantic foundation nodes as prerequisite graph infrastructure and to enforce judge route constraints only on the downstream execution nodes they enable.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_decomposition.py -k semantic_prerequisites_even_when_judge_blocks_execution_node -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/subgraph_planner.py agent_runtime_framework/workflow/planner_prompts.py tests/test_workflow_decomposition.py docs/plans/2026-04-07-agent-graph-semantic-foundation-plan.md
git commit -m "refactor: treat semantic planning as graph foundation"
```

### Task 3: Keep execution nodes consuming semantic state only

**Files:**
- Modify: `agent_runtime_framework/workflow/agent_graph_runtime.py`
- Modify: `agent_runtime_framework/workflow/content_search_executor.py`
- Modify: `agent_runtime_framework/workflow/chunked_file_read_executor.py`
- Modify: `agent_runtime_framework/workflow/target_resolution_executor.py`
- Test: `tests/test_workflow_runtime.py`

**Step 1: Write the failing test**

```python
def test_agent_graph_runtime_search_path_executes_semantic_foundation_before_content_search():
    ...
    assert runtime.calls[0][:3] == ["interpret_target", "plan_search", "content_search"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_runtime.py -k semantic_foundation_before_content_search -v`
Expected: FAIL because runtime currently lets planner jump directly into execution nodes.

**Step 3: Write minimal implementation**

Keep executor contracts strict, but ensure runtime/planner produce the semantic foundation first so execution nodes consume prepared semantic state instead of surfacing missing-plan errors during normal operation.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_runtime.py -k semantic_foundation_before_content_search -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent_runtime_framework/workflow/agent_graph_runtime.py agent_runtime_framework/workflow/content_search_executor.py agent_runtime_framework/workflow/chunked_file_read_executor.py agent_runtime_framework/workflow/target_resolution_executor.py tests/test_workflow_runtime.py
git commit -m "refactor: run semantic foundation before execution nodes"
```
