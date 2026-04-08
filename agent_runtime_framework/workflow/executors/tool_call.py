from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from agent_runtime_framework.workflow.llm.access import get_application_context, get_workspace_context
from typing import Any

from agent_runtime_framework.workflow.runtime.protocols import RuntimeContextLike

from agent_runtime_framework.workflow.workspace.models import TaskState
from agent_runtime_framework.tools import ToolCall, execute_tool_call
from agent_runtime_framework.workflow.state.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class ToolCallExecutor:
    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        runtime_context = dict(context or {})
        application_context = get_application_context(runtime_context)
        workflow_context = get_workspace_context(runtime_context)
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
                output={
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "tool_error": result.error,
                    "tool_metadata": dict(result.metadata or {}),
                    "quality_signals": [{
                        "source": "tool_call",
                        "relevance": "medium",
                        "confidence": 0.75,
                        "progress_contribution": "tool_call_failed",
                        "verification_needed": False,
                        "recoverable_error": True,
                    }],
                    "reasoning_trace": [{"kind": "tool_call", "summary": f"Tool {tool_name} failed"}],
                },
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
            output={
                "tool_name": tool_name,
                "arguments": arguments,
                "tool_output": output,
                "summary": summary,
                "quality_signals": [{
                    "source": "tool_call",
                    "relevance": "medium",
                    "confidence": 0.85,
                    "progress_contribution": "tool_result_collected",
                    "verification_needed": False,
                    "recoverable_error": False,
                }],
                "reasoning_trace": [{"kind": "tool_call", "summary": f"Tool {tool_name} returned structured output"}],
            },
            references=references,
        )
