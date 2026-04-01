from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime_framework.mcp.models import McpCapabilityRef, McpServiceRef
from agent_runtime_framework.skills.models import SkillAttachment


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    agent_id: str
    label: str
    description: str = ""
    kind: str = "agent"
    default_persona: str = "general"
    allowed_tool_names: tuple[str, ...] = ()
    workflow_preferences: tuple[str, ...] = ()
    supports_subagents: bool = False
    executor_kind: str = "workflow"
    default_skills: tuple[SkillAttachment, ...] = ()
    optional_skills: tuple[SkillAttachment, ...] = ()
    allowed_mcp_servers: tuple[McpServiceRef, ...] = ()
    allowed_mcp_capabilities: tuple[McpCapabilityRef, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.agent_id,
            "label": self.label,
            "description": self.description,
            "kind": self.kind,
            "default_persona": self.default_persona,
            "allowed_tool_names": list(self.allowed_tool_names),
            "workflow_preferences": list(self.workflow_preferences),
            "supports_subagents": self.supports_subagents,
            "executor_kind": self.executor_kind,
            "default_skills": [item.to_payload() for item in self.default_skills],
            "optional_skills": [item.to_payload() for item in self.optional_skills],
            "allowed_mcp_servers": [item.to_payload() for item in self.allowed_mcp_servers],
            "allowed_mcp_capabilities": [item.to_payload() for item in self.allowed_mcp_capabilities],
            "metadata": dict(self.metadata),
        }
