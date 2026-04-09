from agent_runtime_framework.workflow import (
    InteractionRequest,
    NODE_STATUS_COMPLETED,
    NODE_STATUS_WAITING_APPROVAL,
    RUN_STATUS_WAITING_APPROVAL,
    RUN_STATUS_WAITING_INPUT,
    NodeState,
    WorkflowGraph,
    WorkflowRun,
)
from agent_runtime_framework.workflow.state.persistence import WorkflowPersistenceStore


def test_workflow_run_can_restore_waiting_approval_state(tmp_path):
    store = WorkflowPersistenceStore(tmp_path / "workflow-runs.json")
    run = WorkflowRun(goal="demo")
    run.status = RUN_STATUS_WAITING_APPROVAL
    run.node_states["first"] = NodeState(node_id="first", status=NODE_STATUS_COMPLETED)
    run.node_states["dangerous"] = NodeState(
        node_id="dangerous",
        status=NODE_STATUS_WAITING_APPROVAL,
        approval_requested=True,
    )
    run.shared_state["resume_token"] = {"token_id": "token-1", "node_id": "dangerous"}

    store.save(run)
    restored = store.load(run.run_id)

    assert restored.run_id == run.run_id
    assert restored.status == RUN_STATUS_WAITING_APPROVAL
    assert restored.node_states["first"].status == NODE_STATUS_COMPLETED
    assert restored.node_states["dangerous"].status == NODE_STATUS_WAITING_APPROVAL
    assert restored.shared_state["resume_token"]["node_id"] == "dangerous"



def test_workflow_persistence_store_round_trips_structured_evidence_payloads(tmp_path):
    from agent_runtime_framework.workflow import NodeResult

    store = WorkflowPersistenceStore(tmp_path / "workflow-runs.json")
    run = WorkflowRun(goal="explain workspace")
    run.shared_state["aggregated_result"] = NodeResult(
        status=NODE_STATUS_COMPLETED,
        output={
            "summaries": ["workspace summary"],
            "facts": [{"kind": "source_root", "path": "src"}],
            "evidence_items": [{"kind": "path", "path": "README.md", "summary": "README"}],
            "chunks": [{"start_line": 1, "end_line": 2, "text": "line1\nline2"}],
            "artifacts": {"tree_sample": ["README.md"]},
            "open_questions": [],
            "verification": {"status": "passed", "success": True, "summary": "verified"},
            "verification_events": [{"status": "passed", "success": True, "summary": "verified"}],
        },
        references=["README.md"],
    )
    run.shared_state["evidence_synthesis"] = {
        "summary": "workspace summary",
        "facts": [{"kind": "source_root", "path": "src"}],
        "evidence_items": [{"kind": "path", "path": "README.md", "summary": "README"}],
    }

    store.save(run)
    restored = store.load(run.run_id)

    assert restored.shared_state["aggregated_result"].output["facts"] == [{"kind": "source_root", "path": "src"}]
    assert restored.shared_state["aggregated_result"].output["evidence_items"][0]["path"] == "README.md"
    assert restored.shared_state["evidence_synthesis"]["summary"] == "workspace summary"


def test_workflow_persistence_store_round_trips_pending_interaction(tmp_path):
    store = WorkflowPersistenceStore(tmp_path / "workflow-runs.json")
    run = WorkflowRun(goal="read readme")
    run.status = RUN_STATUS_WAITING_INPUT
    run.pending_interaction = InteractionRequest(
        kind="clarification",
        prompt="Which README should I inspect?",
        items=["README.md", "frontend-shell/README.md"],
    )

    store.save(run)
    restored = store.load(run.run_id)

    assert restored.status == RUN_STATUS_WAITING_INPUT
    assert restored.pending_interaction is not None
    assert restored.pending_interaction.kind == "clarification"
    assert restored.pending_interaction.items == ["README.md", "frontend-shell/README.md"]


def test_workflow_persistence_store_restores_agent_graph_state_metadata(tmp_path):
    store = WorkflowPersistenceStore(tmp_path / "workflow-runs.json")
    run = WorkflowRun(goal="demo")
    run.graph = WorkflowGraph(metadata={"append_history": [{"iteration": 1, "parent_judge_id": "plan_1", "appended_node_ids": ["content_search_1"]}]})
    run.metadata["agent_graph_state"] = {
        "run_id": run.run_id,
        "current_iteration": 1,
        "goal_envelope": {"goal": "demo", "normalized_goal": "demo", "intent": "file_read", "target_hints": [], "memory_snapshot": {}, "workspace_snapshot": {}, "policy_context": {}, "constraints": {}, "success_criteria": []},
        "aggregated_payload": {"summaries": ["s"], "facts": [], "evidence_items": [], "chunks": [], "artifacts": {}, "open_questions": [], "verification": None, "verification_events": []},
        "execution_summary": {"current_iteration": 1, "summaries": ["s"]},
        "planned_subgraphs": [{"iteration": 1, "planner_summary": "p", "nodes": [], "edges": [], "metadata": {}}],
        "judge_history": [{"status": "accepted", "reason": "ok", "missing_evidence": [], "coverage_report": {}, "replan_hint": {}}],
        "appended_node_ids": ["content_search_1"],
        "memory_state": {
            "clarification_memory": {"active_question": "which readme", "candidate_items": ["README.md"]},
            "semantic_memory": {"confirmed_targets": ["README.md"]},
            "execution_memory": {"ineffective_actions": ["search readme broadly"]},
            "preference_memory": {"path_preferences": ["README.md"]},
        },
    }

    store.save(run)
    restored = store.load(run.run_id)

    assert restored.metadata["agent_graph_state"]["current_iteration"] == 1
    assert restored.metadata["agent_graph_state"]["execution_summary"]["current_iteration"] == 1
    assert restored.metadata["agent_graph_state"]["memory_state"]["semantic_memory"]["confirmed_targets"] == ["README.md"]
    assert restored.graph.metadata["append_history"][0]["parent_judge_id"] == "plan_1"


def test_workflow_persistence_store_omits_runtime_only_shared_state(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    store = WorkflowPersistenceStore(tmp_path / "workflow-runs.json")
    run = WorkflowRun(goal="demo")
    run.shared_state["runtime_context"] = SimpleNamespace(
        workspace_root=Path(tmp_path),
        application_context=SimpleNamespace(config={"default_directory": Path(tmp_path) / "workspace"}),
    )
    run.shared_state["agent_graph_state_ref"] = SimpleNamespace(run_id=run.run_id)
    run.shared_state["session_memory_snapshot"] = SimpleNamespace(last_summary="focused")
    run.shared_state["safe_value"] = {"workspace_root": Path(tmp_path)}

    store.save(run)
    restored = store.load(run.run_id)

    assert "runtime_context" not in restored.shared_state
    assert "agent_graph_state_ref" not in restored.shared_state
    assert "session_memory_snapshot" not in restored.shared_state
    assert restored.shared_state["safe_value"]["workspace_root"] == str(tmp_path)
