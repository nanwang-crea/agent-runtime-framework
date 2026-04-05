from agent_runtime_framework.workflow import (
    NODE_STATUS_COMPLETED,
    NODE_STATUS_WAITING_APPROVAL,
    NodeResult,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
)
from agent_runtime_framework.workflow.execution_runtime import GraphExecutionRuntime


class NoopExecutor:
    def execute(self, node, run, context=None):
        return NodeResult(status=NODE_STATUS_COMPLETED, output={"node": node.node_id})


class ApprovalExecutor:
    def execute(self, node, run, context=None):
        return NodeResult(status="waiting_approval", approval_data={"kind": "custom"}, output={"summary": "needs approval"})

    def resume(self, node, run, prior_result, *, approved, context=None):
        if not approved:
            return NodeResult(status="failed", error="approval rejected")
        return NodeResult(status=NODE_STATUS_COMPLETED, output={"node": node.node_id, "approved": True})


def test_workflow_runtime_resumes_only_waiting_approval_node():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="first", node_type="noop"),
            WorkflowNode(node_id="dangerous", node_type="noop", dependencies=["first"], requires_approval=True),
            WorkflowNode(node_id="finish", node_type="noop", dependencies=["dangerous"]),
        ],
        edges=[
            WorkflowEdge(source="first", target="dangerous"),
            WorkflowEdge(source="dangerous", target="finish"),
        ],
    )
    run = WorkflowRun(goal="demo", graph=graph)
    runtime = GraphExecutionRuntime(executors={"noop": NoopExecutor()})

    first = runtime.run(run)

    assert first.status == "waiting_approval"
    assert first.node_states["first"].status == NODE_STATUS_COMPLETED
    assert first.node_states["dangerous"].status == NODE_STATUS_WAITING_APPROVAL
    assert first.node_states["finish"].status == "pending"
    resume_token = first.shared_state["resume_token"]

    resumed = runtime.resume(first, resume_token=resume_token, approved=True)

    assert resumed.status == "completed"
    assert resumed.node_states["first"].status == NODE_STATUS_COMPLETED
    assert resumed.node_states["dangerous"].status == NODE_STATUS_COMPLETED
    assert resumed.node_states["finish"].status == NODE_STATUS_COMPLETED

def test_workflow_runtime_resumes_explicit_approval_gate_graph():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="workspace_subtask", node_type="noop"),
            WorkflowNode(node_id="approval_gate", node_type="noop", dependencies=["workspace_subtask"], requires_approval=True),
            WorkflowNode(node_id="final_response", node_type="noop", dependencies=["approval_gate"]),
        ],
        edges=[
            WorkflowEdge(source="workspace_subtask", target="approval_gate"),
            WorkflowEdge(source="approval_gate", target="final_response"),
        ],
    )
    run = WorkflowRun(goal="直接删除 README.md", graph=graph)
    runtime = GraphExecutionRuntime(executors={"noop": NoopExecutor()})

    first = runtime.run(run)

    assert first.status == "waiting_approval"
    resume_token = first.shared_state["resume_token"]

    resumed = runtime.resume(first, resume_token=resume_token, approved=True)

    assert resumed.status == "completed"
    assert resumed.node_states["approval_gate"].status == NODE_STATUS_COMPLETED


def test_workflow_runtime_resumes_restored_executor_managed_approval_node():
    from agent_runtime_framework.workflow.agent_graph_state_store import AgentGraphStateStore

    payload = {
        "run_id": "run-approval",
        "goal": "demo",
        "status": "waiting_approval",
        "graph": {
            "nodes": [
                {"node_id": "dangerous", "node_type": "approval_executor"},
                {"node_id": "finish", "node_type": "noop", "dependencies": ["dangerous"]},
            ],
            "edges": [
                {"source": "dangerous", "target": "finish"},
            ],
            "metadata": {},
        },
        "shared_state": {
            "resume_token": {"token_id": "token-1", "node_id": "dangerous"},
            "node_results": {},
        },
        "node_states": {
            "dangerous": {
                "node_id": "dangerous",
                "status": "waiting_approval",
                "result": {
                    "status": "waiting_approval",
                    "output": {"summary": "needs approval"},
                    "approval_data": {"kind": "custom"},
                },
            },
            "finish": {
                "node_id": "finish",
                "status": "pending",
            },
        },
    }

    run = AgentGraphStateStore().restore_workflow_run(payload)
    runtime = GraphExecutionRuntime(executors={"approval_executor": ApprovalExecutor(), "noop": NoopExecutor()})

    resumed = runtime.resume(run, resume_token=run.shared_state["resume_token"], approved=True)

    assert resumed.status == "completed"
    assert resumed.node_states["dangerous"].result.output == {"node": "dangerous", "approved": True}
    assert resumed.node_states["finish"].status == NODE_STATUS_COMPLETED
