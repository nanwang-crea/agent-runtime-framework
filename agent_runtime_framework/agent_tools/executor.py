from __future__ import annotations

from typing import Any

from agent_runtime_framework.agent_tools.models import AgentToolCall, AgentToolResult
from agent_runtime_framework.agent_tools.prompts import build_agent_tool_prompt
from agent_runtime_framework.agents.registry import AgentRegistry


def execute_agent_tool(call: AgentToolCall, registry: AgentRegistry, runtime: Any) -> AgentToolResult:
    definition = registry.require(call.agent_id)
    prompt = build_agent_tool_prompt(call, definition)
    result = runtime(call, definition, prompt)
    if isinstance(result, AgentToolResult):
        return result
    if isinstance(result, dict):
        return AgentToolResult(
            status=str(result.get("status") or "completed"),
            agent_id=definition.agent_id,
            output=str(result.get("output") or result.get("final_answer") or ""),
            execution_backend=str(result.get("execution_backend") or definition.executor_kind),
            metadata=dict(result.get("metadata") or {}),
        )
    return AgentToolResult(status="completed", agent_id=definition.agent_id, output=str(result), execution_backend=definition.executor_kind)
