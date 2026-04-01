from agent_runtime_framework.agents import AgentRegistry, builtin_agent_definitions
from agent_runtime_framework.agent_tools import AgentToolCall


def test_agent_definition_exposes_skill_and_mcp_interfaces():
    registry = AgentRegistry()
    registry.register_many(builtin_agent_definitions())

    definition = registry.require("workspace")

    assert definition.optional_skills
    assert definition.allowed_mcp_servers
    assert definition.allowed_mcp_capabilities


def test_agent_tool_call_carries_extension_hints():
    call = AgentToolCall(
        agent_id="workspace",
        message="inspect repo",
        requested_skills=["repository_overview"],
        enabled_skills=["repository_overview"],
        external_capability_hints=["workspace.search"],
    )

    assert call.requested_skills == ["repository_overview"]
    assert call.external_capability_hints == ["workspace.search"]
