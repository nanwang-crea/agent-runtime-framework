from agent_runtime_framework.agents import AgentRegistry, builtin_agent_definitions


def test_agent_definition_exposes_skill_and_mcp_interfaces():
    registry = AgentRegistry()
    registry.register_many(builtin_agent_definitions())

    definition = registry.require("workspace")

    assert definition.optional_skills
    assert definition.allowed_mcp_servers
    assert definition.allowed_mcp_capabilities
