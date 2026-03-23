from __future__ import annotations

from agent_runtime_framework.core.specs import ToolSpec
from agent_runtime_framework.tools.models import ToolCall, ToolResult


def execute_tool_call(
    tool: ToolSpec,
    call: ToolCall,
    *,
    task,
    context,
) -> ToolResult:
    attempts = 0
    while True:
        attempts += 1
        try:
            output = tool.executor(task, context, call.arguments)
            return ToolResult(
                tool_name=tool.name,
                success=True,
                output=output,
                attempt_count=attempts,
            )
        except Exception as exc:
            if attempts > tool.max_retries:
                return ToolResult(
                    tool_name=tool.name,
                    success=False,
                    error=str(exc),
                    exception=exc,
                    attempt_count=attempts,
                )
