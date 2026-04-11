import pytest

from agent_runtime_framework.capabilities.defaults import build_default_capability_registry
from agent_runtime_framework.observability import InMemoryRunObserver
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.workflow.context.app_context import ApplicationContext
from agent_runtime_framework.workflow.nodes.capability_extension import CapabilityExtensionExecutor
from agent_runtime_framework.workflow.state.approval import APPROVAL_KIND_CAPABILITY_EXTENSION
from agent_runtime_framework.workflow.state.models import WorkflowNode, WorkflowRun


def test_extension_precondition_rejects_existing_capability():
    reg = build_default_capability_registry()
    from agent_runtime_framework.capabilities.extension_policy import CapabilityExtensionRequest, assert_extension_preconditions

    req = CapabilityExtensionRequest(
        proposed_capability_id="read_workspace_evidence",
        rationale="dup",
        extension_kind="macro",
    )
    with pytest.raises(ValueError, match="already registered"):
        assert_extension_preconditions(reg, req)


def test_capability_extension_executor_records_audit_and_observer():
    reg = build_default_capability_registry()
    observer = InMemoryRunObserver()
    app = ApplicationContext(
        resource_repository=LocalFileResourceRepository(["."]),
        services={"capability_registry": reg},
        observer=observer,
    )
    run = WorkflowRun(goal="x", metadata={})
    node = WorkflowNode(
        node_id="ext1",
        node_type="capability_extension",
        metadata={
            "proposed_capability_id": "custom_capability_xyz",
            "rationale": "pilot",
            "extension_kind": "macro",
            "governance_two_phase": False,
        },
    )
    ctx = {"application_context": app, "services": {}}
    result = CapabilityExtensionExecutor().execute(node, run, context=ctx)
    assert result.status == "completed"
    assert run.metadata["capability_extension_audit"][0]["kind"] == APPROVAL_KIND_CAPABILITY_EXTENSION
    assert observer.events[-1].stage == "capability_extension"
