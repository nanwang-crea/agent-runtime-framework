from agent_runtime_framework.workflow import (
    NODE_STATUS_COMPLETED,
    NODE_STATUS_PENDING,
    RUN_STATUS_PENDING,
    NodeResult,
    NodeState,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
)


def test_workflow_run_tracks_graph_and_node_states():
    analyze = WorkflowNode(node_id="analyze", node_type="analysis")
    finish = WorkflowNode(node_id="finish", node_type="final_response")
    graph = WorkflowGraph(
        nodes=[analyze, finish],
        edges=[WorkflowEdge(source="analyze", target="finish")],
    )
    run = WorkflowRun(
        goal="read README and summarize",
        graph=graph,
        node_states={"analyze": NodeState(node_id="analyze")},
    )

    assert run.goal == "read README and summarize"
    assert run.status == RUN_STATUS_PENDING
    assert run.graph is graph
    assert run.shared_state == {}
    assert run.node_states["analyze"].status == NODE_STATUS_PENDING


def test_workflow_node_supports_dependency_metadata_and_execution_policy_fields():
    node = WorkflowNode(
        node_id="summarize",
        node_type="final_response",
        dependencies=["analyze", "readme"],
        task_profile="final_synthesis",
        requires_approval=True,
        retry_limit=2,
        metadata={"audience": "user"},
    )

    assert node.dependencies == ["analyze", "readme"]
    assert node.task_profile == "final_synthesis"
    assert node.requires_approval is True
    assert node.retry_limit == 2
    assert node.metadata == {"audience": "user"}


def test_node_state_and_result_capture_result_error_and_approval_data():
    result = NodeResult(
        status=NODE_STATUS_COMPLETED,
        output={"summary": "done"},
        references=["README.md"],
    )
    state = NodeState(
        node_id="summarize",
        status=NODE_STATUS_COMPLETED,
        result=result,
        error="",
        approval_requested=True,
        approval_granted=False,
    )

    assert state.result is result
    assert state.result.output == {"summary": "done"}
    assert state.result.references == ["README.md"]
    assert state.approval_requested is True
    assert state.approval_granted is False
    assert state.error == ""


def test_workflow_models_expose_stable_status_values():
    node = WorkflowNode(node_id="plan", node_type="planner")
    run = WorkflowRun(goal="plan task")
    result = NodeResult(status=NODE_STATUS_COMPLETED)

    assert RUN_STATUS_PENDING == "pending"
    assert NODE_STATUS_PENDING == "pending"
    assert NODE_STATUS_COMPLETED == "completed"
    assert node.status == NODE_STATUS_PENDING
    assert run.status == RUN_STATUS_PENDING
    assert result.status == NODE_STATUS_COMPLETED
