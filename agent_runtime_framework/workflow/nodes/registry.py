from __future__ import annotations

from agent_runtime_framework.workflow.nodes.core import AggregationExecutor, ApprovalGateExecutor, FinalResponseExecutor, VerificationExecutor
from agent_runtime_framework.workflow.nodes.discovery import (
    ChunkedFileReadExecutor,
    ContentSearchExecutor,
    EvidenceSynthesisExecutor,
    TargetResolutionExecutor,
    WorkspaceDiscoveryExecutor,
)
from agent_runtime_framework.workflow.nodes.interaction import ClarificationExecutor, ToolCallExecutor
from agent_runtime_framework.workflow.nodes.semantic import InterpretTargetExecutor, PlanReadExecutor, PlanSearchExecutor
from agent_runtime_framework.workflow.nodes.capability_diagnosis import CapabilityDiagnosisExecutor
from agent_runtime_framework.workflow.nodes.capability_extension import CapabilityExtensionExecutor
from agent_runtime_framework.workflow.nodes.workspace_write import (
    AppendTextExecutor,
    ApplyPatchExecutor,
    CreatePathExecutor,
    DeletePathExecutor,
    MovePathExecutor,
    WriteFileExecutor,
)


def create_workflow_node_executors() -> dict[str, object]:
    return {
        "workspace_discovery": WorkspaceDiscoveryExecutor(),
        "interpret_target": InterpretTargetExecutor(),
        "plan_search": PlanSearchExecutor(),
        "plan_read": PlanReadExecutor(),
        "content_search": ContentSearchExecutor(),
        "chunked_file_read": ChunkedFileReadExecutor(),
        "evidence_synthesis": EvidenceSynthesisExecutor(),
        "aggregate_results": AggregationExecutor(),
        "create_path": CreatePathExecutor(),
        "move_path": MovePathExecutor(),
        "delete_path": DeletePathExecutor(),
        "apply_patch": ApplyPatchExecutor(),
        "write_file": WriteFileExecutor(),
        "append_text": AppendTextExecutor(),
        "verification": VerificationExecutor(),
        "approval_gate": ApprovalGateExecutor(),
        "final_response": FinalResponseExecutor(),
        "tool_call": ToolCallExecutor(),
        "clarification": ClarificationExecutor(),
        "target_resolution": TargetResolutionExecutor(),
        "capability_diagnosis": CapabilityDiagnosisExecutor(),
        "capability_extension": CapabilityExtensionExecutor(),
    }
