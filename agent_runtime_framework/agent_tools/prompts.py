from __future__ import annotations

from agent_runtime_framework.agents.definitions import AgentDefinition
from agent_runtime_framework.agent_tools.models import AgentToolCall


def build_agent_tool_prompt(call: AgentToolCall, definition: AgentDefinition) -> str:
    extras = []
    if call.enabled_skills:
        extras.append(f"skills={', '.join(call.enabled_skills)}")
    if call.external_capability_hints:
        extras.append(f"external={', '.join(call.external_capability_hints)}")
    suffix = f" ({'; '.join(extras)})" if extras else ""
    return f"Run agent {definition.agent_id} for message: {call.message}{suffix}"
