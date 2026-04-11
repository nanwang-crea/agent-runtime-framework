from types import SimpleNamespace

from agent_runtime_framework.workflow.nodes.capability_diagnosis import CapabilityDiagnosisExecutor
from agent_runtime_framework.workflow.state.models import GoalEnvelope, WorkflowNode, WorkflowRun, new_agent_graph_state


def test_capability_diagnosis_executor_emits_preferred_capabilities():
    goal = GoalEnvelope(goal="demo", normalized_goal="demo", intent="compound")
    state = new_agent_graph_state(run_id="cd-1", goal_envelope=goal)
    state.failure_history.append(
        {
            "iteration": 1,
            "status": "replan",
            "reason": "need read",
            "failure_diagnosis": {
                "category": "tool_validation",
                "subcategory": "missing_required_argument",
                "summary": "missing path",
                "blocking_issue": "path",
                "recoverable": True,
                "suggested_recovery_mode": "repair_arguments",
            },
        }
    )
    run = WorkflowRun(goal="demo", shared_state={"agent_graph_state_ref": state, "node_results": {}})
    node = WorkflowNode(
        node_id="cd1",
        node_type="capability_diagnosis",
        metadata={"preferred_capability_ids": ["read_workspace_evidence"]},
    )
    ctx = SimpleNamespace(services={})
    result = CapabilityDiagnosisExecutor().execute(node, run, context=ctx)
    assert result.status == "completed"
    assert "read_workspace_evidence" in result.output["preferred_capability_ids"]
