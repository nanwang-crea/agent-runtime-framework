from agent_runtime_framework.workflow import (
    NODE_STATUS_COMPLETED,
    NODE_STATUS_WAITING_APPROVAL,
    RUN_STATUS_WAITING_APPROVAL,
    NodeState,
    WorkflowRun,
)
from agent_runtime_framework.workflow.persistence import WorkflowPersistenceStore


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
    run.shared_state["response_synthesis"] = {
        "summary": "workspace summary",
        "facts": [{"kind": "source_root", "path": "src"}],
        "evidence_items": [{"kind": "path", "path": "README.md", "summary": "README"}],
    }

    store.save(run)
    restored = store.load(run.run_id)

    assert restored.shared_state["aggregated_result"].output["facts"] == [{"kind": "source_root", "path": "src"}]
    assert restored.shared_state["aggregated_result"].output["evidence_items"][0]["path"] == "README.md"
    assert restored.shared_state["response_synthesis"]["summary"] == "workspace summary"
