from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from agent_runtime_framework.agents.workspace_backend.models import TaskState
from agent_runtime_framework.tools import ToolCall, execute_tool_call
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class ToolCallExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        runtime_context = dict(context or {})
        application_context = runtime_context.get("application_context")
        workflow_context = runtime_context.get("workspace_context")
        if application_context is None:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing application_context for tool_call executor")

        tool_name = str(node.metadata.get("tool_name") or "").strip()
        if not tool_name:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing tool_name")

        try:
            tool = application_context.tools.require(tool_name)
        except Exception as exc:
            return NodeResult(status=NODE_STATUS_FAILED, error=str(exc))

        arguments = dict(node.metadata.get("arguments") or {})
        task = SimpleNamespace(task_id=node.node_id, goal=run.goal, state=TaskState(resolved_target=str(node.metadata.get("target_path") or "")))
        execution_context = workflow_context or SimpleNamespace(application_context=application_context, services={})
        result = execute_tool_call(tool, ToolCall(tool_name=tool_name, arguments=arguments), task=task, context=execution_context)
        if not result.success:
            return NodeResult(
                status=NODE_STATUS_FAILED,
                output={"tool_name": tool_name, "arguments": arguments, "tool_error": result.error, "tool_metadata": dict(result.metadata or {})},
                error=str(result.error or "tool execution failed"),
            )

        output = dict(result.output or {})
        summary = str(output.get("summary") or output.get("text") or output.get("content") or output.get("stdout") or "")
        references: list[str] = []
        for key in ("path", "resolved_path", "source"):
            value = str(output.get(key) or "").strip()
            if value and value not in references:
                references.append(value)
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={"tool_name": tool_name, "arguments": arguments, "tool_output": output, "summary": summary},
            references=references,
        )
