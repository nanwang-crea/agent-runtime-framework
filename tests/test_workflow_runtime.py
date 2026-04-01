from agent_runtime_framework.workflow import (
    NODE_STATUS_COMPLETED,
    NODE_STATUS_FAILED,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_WAITING_APPROVAL,
    NodeResult,
    NodeState,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
)
from agent_runtime_framework.workflow.runtime import WorkflowRuntime
from agent_runtime_framework.workflow.scheduler import WorkflowScheduler
from agent_runtime_framework.workflow.tool_call_executor import ToolCallExecutor
from agent_runtime_framework.workflow.clarification_executor import ClarificationExecutor
from agent_runtime_framework.workflow.target_resolution_executor import TargetResolutionExecutor
from agent_runtime_framework.workflow.file_inspection_executor import FileInspectionExecutor
from agent_runtime_framework.workflow.response_synthesis_executor import ResponseSynthesisExecutor
from agent_runtime_framework.workflow.node_executors import FinalResponseExecutor
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.core.specs import ToolSpec
from types import SimpleNamespace
from agent_runtime_framework.resources import LocalFileResourceRepository


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


class ApprovalExecutor:
    def execute(self, node, run, context=None):
        return NodeResult(status="waiting_approval", approval_data={"kind": "custom"}, output={"summary": "needs approval"})

    def resume(self, node, run, prior_result, *, approved, context=None):
        if not approved:
            return NodeResult(status=NODE_STATUS_FAILED, error="approval rejected")
        return NodeResult(status=NODE_STATUS_COMPLETED, output={"node": node.node_id, "approved": True})


def test_runtime_resumes_executor_managed_approval_node():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="change", node_type="approval_executor"),
            WorkflowNode(node_id="finish", node_type="noop", dependencies=["change"]),
        ],
        edges=[WorkflowEdge(source="change", target="finish")],
    )
    run = WorkflowRun(goal="demo", graph=graph)
    runtime = WorkflowRuntime(executors={"approval_executor": ApprovalExecutor(), "noop": NoopExecutor()})

    first = runtime.run(run)

    assert first.status == RUN_STATUS_WAITING_APPROVAL
    resume_token = first.shared_state["resume_token"]

    resumed = runtime.resume(first, resume_token=resume_token, approved=True)

    assert resumed.status == RUN_STATUS_COMPLETED
    assert resumed.node_states["change"].result.output == {"node": "change", "approved": True}
    assert resumed.node_states["finish"].status == NODE_STATUS_COMPLETED



def test_single_node_workflow_run_initializes_and_completes_state_tracking():
    graph = WorkflowGraph(nodes=[WorkflowNode(node_id="only", node_type="noop")], edges=[])
    run = WorkflowRun(goal="demo", graph=graph)

    result = WorkflowRuntime(executors={"noop": NoopExecutor()}).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["only"].status == NODE_STATUS_COMPLETED
    assert result.shared_state["node_results"]["only"].output == {"node": "only"}



def test_runtime_executes_tool_call_node_with_registered_tool():
    def _echo_tool(_task, _context, arguments):
        return {"summary": f"echo:{arguments['text']}"}

    tool_registry = ToolRegistry([
        ToolSpec(
            name="echo_tool",
            description="Echo input",
            executor=_echo_tool,
            required_arguments=("text",),
        )
    ])
    app_context = SimpleNamespace(tools=tool_registry, services={})
    workflow_context = SimpleNamespace(application_context=app_context, services={})
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="call", node_type="tool_call", metadata={"tool_name": "echo_tool", "arguments": {"text": "hello"}}),
            WorkflowNode(node_id="finish", node_type="final_response", dependencies=["call"]),
        ],
        edges=[WorkflowEdge(source="call", target="finish")],
    )
    run = WorkflowRun(goal="echo", graph=graph)

    result = WorkflowRuntime(
        executors={"tool_call": ToolCallExecutor(), "final_response": FinalResponseExecutor()},
        context={"application_context": app_context, "workspace_context": workflow_context},
    ).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["call"].result.output["tool_name"] == "echo_tool"
    assert result.node_states["call"].result.output["summary"] == "echo:hello"
    assert result.final_output == "echo:hello"


def test_runtime_executes_clarification_node_inside_workflow():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="clarify", node_type="clarification", metadata={"prompt": "Please clarify the target file."}),
            WorkflowNode(node_id="finish", node_type="final_response", dependencies=["clarify"]),
        ],
        edges=[WorkflowEdge(source="clarify", target="finish")],
    )
    run = WorkflowRun(goal="clarify", graph=graph)

    result = WorkflowRuntime(
        executors={"clarification": ClarificationExecutor(), "final_response": FinalResponseExecutor()}
    ).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["clarify"].result.output["clarification_required"] is True
    assert result.shared_state["clarification_request"]["prompt"] == "Please clarify the target file."
    assert "Please clarify the target file." in result.final_output



def test_runtime_executes_target_explainer_node_chain_with_workspace_tools(tmp_path):
    package = tmp_path / "src"
    package.mkdir()
    target = package / "service.py"
    target.write_text("def run():\n    return 'ok'\n", encoding="utf-8")

    from agent_runtime_framework.agents.workspace_backend import build_default_workspace_tools, WorkspaceContext
    from agent_runtime_framework.applications import ApplicationContext

    app_context = ApplicationContext(resource_repository=LocalFileResourceRepository([tmp_path]), config={"default_directory": str(tmp_path)})
    for tool in build_default_workspace_tools():
        app_context.tools.register(tool)
    workspace_context = WorkspaceContext(application_context=app_context)
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(node_id="resolve", node_type="target_resolution", metadata={"query": "请讲解 service 这个模块在做什么"}),
            WorkflowNode(node_id="inspect", node_type="file_inspection", dependencies=["resolve"]),
            WorkflowNode(node_id="synthesize", node_type="response_synthesis", dependencies=["inspect"]),
            WorkflowNode(node_id="finish", node_type="final_response", dependencies=["synthesize"]),
        ],
        edges=[
            WorkflowEdge(source="resolve", target="inspect"),
            WorkflowEdge(source="inspect", target="synthesize"),
            WorkflowEdge(source="synthesize", target="finish"),
        ],
    )
    run = WorkflowRun(goal="请讲解 service 这个模块在做什么", graph=graph)

    result = WorkflowRuntime(
        executors={
            "target_resolution": TargetResolutionExecutor(),
            "file_inspection": FileInspectionExecutor(),
            "response_synthesis": ResponseSynthesisExecutor(),
            "final_response": FinalResponseExecutor(),
        },
        context={"application_context": app_context, "workspace_context": workspace_context, "workspace_root": str(tmp_path)},
    ).run(run)

    assert result.status == RUN_STATUS_COMPLETED
    assert result.node_states["resolve"].result.output["resolution_status"] == "resolved"
    assert result.node_states["inspect"].result.output["path"] == "src/service.py"
    assert "src/service.py" in result.final_output

