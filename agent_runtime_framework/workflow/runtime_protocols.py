from __future__ import annotations

from typing import Any, Protocol

from agent_runtime_framework.workflow.context_assembly import WorkflowRuntimeContext
from agent_runtime_framework.workflow.models import NodeResult, WorkflowNode, WorkflowRun


RuntimeContextLike = WorkflowRuntimeContext | dict[str, Any] | None


class WorkflowNodeExecutor(Protocol):
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult: ...


class ResumableWorkflowNodeExecutor(WorkflowNodeExecutor, Protocol):
    def resume(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        prior_result: NodeResult,
        *,
        approved: bool,
        context: RuntimeContextLike = None,
    ) -> NodeResult: ...
