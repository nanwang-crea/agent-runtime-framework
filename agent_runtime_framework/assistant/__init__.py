"""Single-agent assistant runtime with capability, skill, and MCP slots."""

from agent_runtime_framework.assistant.capabilities import CapabilityRegistry
from agent_runtime_framework.assistant.loop import AgentLoop, AgentLoopResult, AssistantContext
from agent_runtime_framework.assistant.mcp import MCPClient, MCPClientAdapter, MCPProvider, MCPToolSpec, StaticMCPProvider
from agent_runtime_framework.assistant.session import AssistantSession, AssistantTurn
from agent_runtime_framework.assistant.skills import SkillRegistry, SkillSpec

__all__ = [
    "AgentLoop",
    "AgentLoopResult",
    "AssistantContext",
    "AssistantSession",
    "AssistantTurn",
    "CapabilityRegistry",
    "MCPClient",
    "MCPClientAdapter",
    "MCPProvider",
    "MCPToolSpec",
    "SkillRegistry",
    "SkillSpec",
    "StaticMCPProvider",
]
