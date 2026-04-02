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



def test_workflow_runtime_resumes_graph_built_approval_gate_for_workspace_subtask():
    from agent_runtime_framework.workflow.graph_builder import compile_compat_workflow_graph
    from agent_runtime_framework.workflow.models import GoalSpec

    goal = GoalSpec(
        original_goal="直接删除 README.md",
        primary_intent="dangerous_change",
        metadata={"requires_approval": True},
    )
    graph = compile_compat_workflow_graph(goal)
    run = WorkflowRun(goal=goal.original_goal, graph=graph)
    runtime = GraphExecutionRuntime(executors={"noop": NoopExecutor(), "workspace_subtask": NoopExecutor(), "approval_gate": NoopExecutor(), "final_response": NoopExecutor()})

    first = runtime.run(run)

    assert first.status == "waiting_approval"
    resume_token = first.shared_state["resume_token"]

    resumed = runtime.resume(first, resume_token=resume_token, approved=True)

    assert resumed.status == "completed"
    assert resumed.node_states["approval_gate"].status == NODE_STATUS_COMPLETED
