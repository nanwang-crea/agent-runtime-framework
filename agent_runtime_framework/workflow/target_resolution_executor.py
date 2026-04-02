from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from agent_runtime_framework.workflow.llm_access import get_application_context, get_workspace_context
from typing import Any

from agent_runtime_framework.workflow.runtime_protocols import RuntimeContextLike

from agent_runtime_framework.agents.workspace_backend.models import TaskState
from agent_runtime_framework.tools import ToolCall, execute_tool_call
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class TargetResolutionExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        runtime_context = dict(context or {})
        application_context = runtime_context.get("application_context")
        workspace_context = runtime_context.get("workspace_context")
        if application_context is None:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing application_context for target_resolution executor")

        tool = application_context.tools.require("resolve_workspace_target")
        query = str(node.metadata.get("query") or run.goal)
        target_hint = str(node.metadata.get("target_hint") or "")
        task = SimpleNamespace(task_id=node.node_id, goal=run.goal, state=TaskState())
        execution_context = workspace_context or SimpleNamespace(application_context=application_context, services={})
        result = execute_tool_call(
            tool,
            ToolCall(tool_name="resolve_workspace_target", arguments={"query": query, "target_hint": target_hint}),
            task=task,
            context=execution_context,
        )
        if not result.success:
            return NodeResult(status=NODE_STATUS_FAILED, error=str(result.error or "target resolution failed"))

        output = dict(result.output or {})
        if str(output.get("resolution_status") or "") in {"ambiguous", "unresolved"}:
            output["clarification_required"] = True
            run.shared_state["clarification_request"] = {
                "prompt": str(output.get("text") or output.get("summary") or "Please clarify the target."),
                "items": list(output.get("items") or []),
            }
        run.shared_state["resolved_target"] = output
        references = [value for value in [str(output.get("resolved_path") or "").strip(), str(output.get("path") or "").strip()] if value]
        return NodeResult(status=NODE_STATUS_COMPLETED, output=output, references=references)
