from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from agent_runtime_framework.agents.workspace_backend.models import TaskState
from agent_runtime_framework.tools import ToolCall, execute_tool_call
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class FileInspectionExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        runtime_context = dict(context or {})
        application_context = runtime_context.get("application_context")
        workspace_context = runtime_context.get("workspace_context")
        if application_context is None:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing application_context for file_inspection executor")

        resolved = dict(run.shared_state.get("resolved_target") or {})
        resolution_status = str(resolved.get("resolution_status") or "")
        if resolution_status in {"ambiguous", "unresolved"}:
            text = str(resolved.get("text") or resolved.get("summary") or "Please clarify the target.")
            output = {"summary": text, "text": text, "clarification_required": True, **resolved}
            return NodeResult(status=NODE_STATUS_COMPLETED, output=output)

        target_path = str(resolved.get("resolved_path") or node.metadata.get("target_path") or "").strip()
        if not target_path:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing resolved target path")

        resolved_kind = str(resolved.get("resolved_kind") or "file")
        if resolved_kind == "directory":
            tool_name = "inspect_workspace_path"
        else:
            tool_name = "summarize_workspace_text" if application_context.tools.get("summarize_workspace_text") is not None else "read_workspace_text"

        tool = application_context.tools.require(tool_name)
        task = SimpleNamespace(task_id=node.node_id, goal=run.goal, state=TaskState(resolved_target=target_path))
        execution_context = workspace_context or SimpleNamespace(application_context=application_context, services={})
        result = execute_tool_call(tool, ToolCall(tool_name=tool_name, arguments={"path": target_path}), task=task, context=execution_context)
        if not result.success:
            return NodeResult(status=NODE_STATUS_FAILED, error=str(result.error or "file inspection failed"))

        output = dict(result.output or {})
        output.setdefault("path", target_path)
        output.setdefault("resolved_kind", resolved_kind)
        output.setdefault("summary", str(output.get("text") or output.get("summary") or target_path))
        references = [str(output.get("path") or target_path)]
        run.shared_state["inspection_result"] = output
        return NodeResult(status=NODE_STATUS_COMPLETED, output=output, references=references)
