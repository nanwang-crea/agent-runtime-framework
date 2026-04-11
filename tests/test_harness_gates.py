from agent_runtime_framework.capabilities.defaults import build_default_capability_registry
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.workflow.context.app_context import ApplicationContext
from agent_runtime_framework.workflow.nodes.capability_extension import CapabilityExtensionExecutor
from agent_runtime_framework.workflow.nodes.core import VerificationExecutor
from agent_runtime_framework.workflow.planning.subgraph_planner import (
    _inject_post_recovery_verification_if_needed,
    _should_force_verification_gate,
)
from agent_runtime_framework.workflow.runtime.execution import GraphExecutionRuntime
from agent_runtime_framework.workflow.state.models import (
    GoalEnvelope,
    NODE_STATUS_COMPLETED,
    NodeResult,
    PlannedNode,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    RUN_STATUS_WAITING_APPROVAL,
    new_agent_graph_state,
)
def test_verification_gate_triggers_when_change_intent_pending():
    goal = GoalEnvelope(goal="edit", normalized_goal="edit", intent="change_and_verify")
    state = new_agent_graph_state(run_id="hg-1", goal_envelope=goal)
    ctx = {"services": {}}
    assert _should_force_verification_gate(state, ctx) is True
    nodes = [PlannedNode(node_id="w_1", node_type="write_file", reason="write", success_criteria=["write"])]
    out = _inject_post_recovery_verification_if_needed(state, nodes, 1, ctx)
    assert out[-1].node_type in ("verification", "verification_step")
    assert out[-1].depends_on == ["w_1"]


def test_verification_gate_disabled_via_service():
    goal = GoalEnvelope(goal="edit", normalized_goal="edit", intent="change_and_verify")
    state = new_agent_graph_state(run_id="hg-2", goal_envelope=goal)
    ctx = {"services": {"harness_verification_gate": False}}
    assert _should_force_verification_gate(state, ctx) is False


def test_verification_executor_failed_output_includes_recipe_recovery_mode():
    node = WorkflowNode(
        node_id="v1",
        node_type="verification",
        metadata={"verification_recipe_id": "post_write_workspace_path"},
    )
    run = WorkflowRun(
        goal="demo",
        shared_state={
            "node_results": {
                "other": NodeResult(
                    status=NODE_STATUS_COMPLETED,
                    output={
                        "verification_events": [
                            {
                                "verification_type": "post_write",
                                "status": "failed",
                                "success": False,
                                "summary": "bad",
                            }
                        ]
                    },
                )
            }
        },
    )
    result = VerificationExecutor().execute(node, run, context={"workspace_root": "."})
    assert result.status == "failed"
    assert result.output.get("on_failure_recovery_mode") == "repair_arguments"


def test_capability_extension_two_phase_resume():
    reg = build_default_capability_registry()
    app = ApplicationContext(
        resource_repository=LocalFileResourceRepository(["."]),
        services={"capability_registry": reg},
    )
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(
                node_id="ext1",
                node_type="capability_extension",
                metadata={
                    "proposed_capability_id": "custom_extension_cap_99",
                    "rationale": "integration",
                    "extension_kind": "macro",
                    "governance_two_phase": True,
                },
            )
        ],
        edges=[],
    )
    run = WorkflowRun(goal="demo", graph=graph, metadata={})
    ctx = {"application_context": app, "services": {}}
    runtime = GraphExecutionRuntime(executors={"capability_extension": CapabilityExtensionExecutor()}, context=ctx)
    first = runtime.run(run)
    assert first.status == RUN_STATUS_WAITING_APPROVAL
    token = first.shared_state["resume_token"]
    resumed = runtime.resume(first, resume_token=token, approved=True)
    assert resumed.status == "completed"
    assert resumed.metadata["capability_extension_audit"][-1]["proposed_capability_id"] == "custom_extension_cap_99"
