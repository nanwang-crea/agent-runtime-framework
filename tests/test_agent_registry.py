from agent_runtime_framework.agents import AgentDefinition, AgentRegistry, builtin_agent_definitions


def test_builtin_agent_registry_contains_core_agents():
    registry = AgentRegistry()
    registry.register_many(builtin_agent_definitions())

    assert registry.require("workspace").label == "Workspace Agent"
    assert registry.require("qa_only").executor_kind == "conversation"
    assert registry.require("explore").default_persona == "explore"
    assert registry.require("verification").supports_subagents is False


def test_agent_definition_payload_includes_extension_slots():
    definition = AgentRegistry()
    definition.register_many(builtin_agent_definitions())

    payload = definition.require("workspace").to_payload()

    assert "default_skills" in payload
    assert "optional_skills" in payload
    assert "allowed_mcp_servers" in payload
    assert "allowed_mcp_capabilities" in payload
