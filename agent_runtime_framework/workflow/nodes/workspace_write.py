from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from agent_runtime_framework.workflow.workspace.models import TaskState
from agent_runtime_framework.tools import ToolCall, execute_tool_call
from agent_runtime_framework.workflow.llm_access import get_application_context, get_workspace_context
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun
from agent_runtime_framework.workflow.runtime_protocols import RuntimeContextLike


def _collect_references(output: dict[str, Any]) -> list[str]:
    references: list[str] = []
    for key in ("path", "resolved_path", "source"):
        value = str(output.get(key) or "").strip()
        if value and value not in references:
            references.append(value)
    for value in output.get("changed_paths") or []:
        text = str(value).strip()
        if text and text not in references:
            references.append(text)
    return references


def _workspace_quality_signal(tool_name: str, success: bool, summary: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contribution = "workspace_updated" if success else "workspace_update_failed"
    return (
        [
            {
                "source": tool_name,
                "relevance": "high",
                "confidence": 0.95 if success else 0.8,
                "progress_contribution": contribution,
                "verification_needed": success,
                "recoverable_error": not success,
            }
        ],
        [{"kind": "workspace_change", "summary": summary}],
    )


@dataclass(slots=True)
class WorkspaceToolNodeExecutor:
    tool_name: str
    argument_keys: tuple[str, ...]

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        runtime_context = dict(context or {})
        application_context = get_application_context(runtime_context)
        workspace_context = get_workspace_context(runtime_context)
        if application_context is None:
            return NodeResult(status=NODE_STATUS_FAILED, error=f"Missing application_context for {node.node_type} executor")

        try:
            tool = application_context.tools.require(self.tool_name)
        except Exception as exc:
            return NodeResult(status=NODE_STATUS_FAILED, error=str(exc))

        arguments = {
            key: value
            for key in self.argument_keys
            if (value := node.metadata.get(key)) is not None
        }
        task = SimpleNamespace(
            task_id=node.node_id,
            goal=run.goal,
            state=TaskState(resolved_target=str(node.metadata.get("path") or "")),
        )
        execution_context = workspace_context or SimpleNamespace(application_context=application_context, services={})
        result = execute_tool_call(tool, ToolCall(tool_name=self.tool_name, arguments=arguments), task=task, context=execution_context)
        if not result.success:
            quality_signals, reasoning_trace = _workspace_quality_signal(self.tool_name, False, str(result.error or "tool execution failed"))
            return NodeResult(
                status=NODE_STATUS_FAILED,
                output={
                    "tool_name": self.tool_name,
                    "arguments": arguments,
                    "tool_error": result.error,
                    "tool_metadata": dict(result.metadata or {}),
                    "quality_signals": quality_signals,
                    "reasoning_trace": reasoning_trace,
                },
                error=str(result.error or "tool execution failed"),
            )

        output = dict(result.output or {})
        summary = str(output.get("summary") or output.get("text") or output.get("content") or output.get("stdout") or "")
        quality_signals, reasoning_trace = _workspace_quality_signal(self.tool_name, True, summary)
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "tool_name": self.tool_name,
                "arguments": arguments,
                "tool_output": output,
                "summary": summary,
                "quality_signals": quality_signals,
                "reasoning_trace": reasoning_trace,
            },
            references=_collect_references(output),
        )


class CreatePathExecutor(WorkspaceToolNodeExecutor):
    def __init__(self) -> None:
        super().__init__(tool_name="create_workspace_path", argument_keys=("path", "kind", "content"))


class MovePathExecutor(WorkspaceToolNodeExecutor):
    def __init__(self) -> None:
        super().__init__(tool_name="move_workspace_path", argument_keys=("path", "destination_path"))


class DeletePathExecutor(WorkspaceToolNodeExecutor):
    def __init__(self) -> None:
        super().__init__(tool_name="delete_workspace_path", argument_keys=("path",))


class ApplyPatchExecutor(WorkspaceToolNodeExecutor):
    def __init__(self) -> None:
        super().__init__(tool_name="apply_text_patch", argument_keys=("path", "search_text", "replace_text"))


class WriteFileExecutor(WorkspaceToolNodeExecutor):
    def __init__(self) -> None:
        super().__init__(tool_name="edit_workspace_text", argument_keys=("path", "content"))


class AppendTextExecutor(WorkspaceToolNodeExecutor):
    def __init__(self) -> None:
        super().__init__(tool_name="append_workspace_text", argument_keys=("path", "content"))


__all__ = [
    "WorkspaceToolNodeExecutor",
    "CreatePathExecutor",
    "MovePathExecutor",
    "DeletePathExecutor",
    "ApplyPatchExecutor",
    "WriteFileExecutor",
    "AppendTextExecutor",
]
