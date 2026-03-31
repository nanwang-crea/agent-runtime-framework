from agent_runtime_framework.workflow import (
    NODE_STATUS_COMPLETED,
    NODE_STATUS_FAILED,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    NodeResult,
    NodeState,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
)
from agent_runtime_framework.workflow.runtime import WorkflowRuntime
from agent_runtime_framework.workflow.scheduler import WorkflowScheduler


class NoopExecutor:
    def execute(self, node, run):
        return NodeResult(status=NODE_STATUS_COMPLETED, output={"node": node.node_id})


class FailExecutor:
    def execute(self, node, run):
        return NodeResult(status=NODE_STATUS_FAILED, error="boom")


def test_scheduler_only_returns_nodes_with_completed_dependencies():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="first", node_type="noop"),
            WorkflowNode(node_id="second", node_type="noop", dependencies=["first"]),
        ],
        edges=[WorkflowEdge(source="first", target="second")],
    )
    run = WorkflowRun(
        goal="demo",
        graph=graph,
        node_states={
            "first": NodeState(node_id="first"),
            "second": NodeState(node_id="second"),
        },
    )

    ready_before = WorkflowScheduler().ready_nodes(run)
    run.node_states["first"].status = NODE_STATUS_COMPLETED
    ready_after = WorkflowScheduler().ready_nodes(run)

    assert [node.node_id for node in ready_before] == ["first"]
    assert [node.node_id for node in ready_after] == ["second"]


def test_runtime_executes_ready_nodes_in_dependency_order():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="start", node_type="noop"),
            WorkflowNode(node_id="finish", node_type="noop", dependencies=["start"]),
        ],
        edges=[WorkflowEdge(source="start", target="finish")],
    )
    run = WorkflowRun(goal="demo", graph=graph)

    result = WorkflowRuntime(executors={"noop": NoopExecutor()}).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["start"].status == NODE_STATUS_COMPLETED
    assert result.node_states["finish"].status == NODE_STATUS_COMPLETED
    assert result.node_states["finish"].result.output == {"node": "finish"}


def test_failed_node_stops_downstream_execution():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="start", node_type="fail"),
            WorkflowNode(node_id="finish", node_type="noop", dependencies=["start"]),
        ],
        edges=[WorkflowEdge(source="start", target="finish")],
    )
    run = WorkflowRun(goal="demo", graph=graph)

    result = WorkflowRuntime(
        executors={"fail": FailExecutor(), "noop": NoopExecutor()}
    ).run(run)

    assert result.status == RUN_STATUS_FAILED
    assert result.node_states["start"].status == NODE_STATUS_FAILED
    assert result.node_states["finish"].status != NODE_STATUS_COMPLETED
    assert result.node_states["finish"].result is None
