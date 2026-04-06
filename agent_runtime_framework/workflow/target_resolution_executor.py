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
        interpreted_target = dict(run.shared_state.get("interpreted_target") or {})
        if not interpreted_target:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing interpreted_target")
        preferred_path = str(interpreted_target.get("preferred_path") or "").strip()
        if not preferred_path:
            return NodeResult(status=NODE_STATUS_FAILED, error="interpreted_target missing preferred_path")
        query = preferred_path
        target_hint = preferred_path
        task = SimpleNamespace(task_id=node.node_id, goal=run.goal, state=TaskState())
        execution_context = workspace_context or SimpleNamespace(application_context=application_context, services={})
        result = execute_tool_call(
            tool,
            ToolCall(
                tool_name="resolve_workspace_target",
                arguments={
                    "query": query,
                    "target_hint": target_hint,
                    "preferred_path": interpreted_target.get("preferred_path"),
                    "scope_preference": interpreted_target.get("scope_preference"),
                    "exclude_paths": list(interpreted_target.get("exclude_paths") or []),
                },
            ),
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
        resolution_status = str(output.get("resolution_status") or "").strip()
        output["quality_signals"] = [{
            "source": "target_resolution",
            "relevance": "high",
            "confidence": 0.9 if resolution_status == "resolved" else 0.6,
            "progress_contribution": "target_resolved" if resolution_status == "resolved" else "target_clarification_required",
            "verification_needed": False,
            "recoverable_error": resolution_status in {"ambiguous", "unresolved"},
        }]
        output["reasoning_trace"] = [{
            "kind": "target_resolution",
            "summary": f"Target resolution status: {resolution_status or 'unknown'}",
        }]
        if resolution_status in {"ambiguous", "unresolved"}:
            output["conflicts"] = [str(output.get("summary") or output.get("text") or "Target remains ambiguous.")]
        return NodeResult(status=NODE_STATUS_COMPLETED, output=output, references=references)
