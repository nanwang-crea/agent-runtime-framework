from __future__ import annotations

from agent_runtime_framework.agents.definitions import AgentDefinition
from agent_runtime_framework.mcp.models import McpCapabilityRef, McpServiceRef
from agent_runtime_framework.skills.models import SkillAttachment


def builtin_agent_definitions() -> list[AgentDefinition]:
    workspace_mcp = McpServiceRef(server_id="workspace", label="Workspace")
    read_cap = McpCapabilityRef(server_id="workspace", capability_id="read")
    search_cap = McpCapabilityRef(server_id="workspace", capability_id="search")
    repo_skill = SkillAttachment(skill_id="repository_overview", required=False)
    verify_skill = SkillAttachment(skill_id="verification_protocol", required=False)
    return [
        AgentDefinition(
            agent_id="workspace",
            label="Workspace Agent",
            description="General-purpose workspace agent.",
            kind="agent",
            default_persona="plan",
            workflow_preferences=("workflow_first",),
            supports_subagents=True,
            executor_kind="workflow",
            optional_skills=(repo_skill, verify_skill),
            allowed_mcp_servers=(workspace_mcp,),
            allowed_mcp_capabilities=(read_cap, search_cap),
        ),
        AgentDefinition(
            agent_id="qa_only",
            label="Q&A",
            description="Conversation-focused assistant.",
            kind="chat",
            default_persona="general",
            executor_kind="conversation",
        ),
        AgentDefinition(
            agent_id="explore",
            label="Explore Agent",
            description="Repository exploration specialist.",
            default_persona="explore",
            workflow_preferences=("workspace_discovery", "workspace_read", "compound_read"),
            executor_kind="workflow",
            optional_skills=(repo_skill,),
            allowed_mcp_servers=(workspace_mcp,),
            allowed_mcp_capabilities=(read_cap, search_cap),
        ),
        AgentDefinition(
            agent_id="plan",
            label="Plan Agent",
            description="Planning specialist.",
            default_persona="plan",
            workflow_preferences=("plan",),
            executor_kind="workflow",
        ),
        AgentDefinition(
            agent_id="verification",
            label="Verification Agent",
            description="Verification specialist.",
            default_persona="summary",
            workflow_preferences=("test_and_verify",),
            executor_kind="workflow",
            default_skills=(verify_skill,),
        ),
        AgentDefinition(
            agent_id="conversation",
            label="Conversation Agent",
            description="Direct response assistant.",
            kind="chat",
            default_persona="general",
            executor_kind="conversation",
        ),
    ]
