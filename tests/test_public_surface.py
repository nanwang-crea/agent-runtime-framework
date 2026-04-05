import agent_runtime_framework as arf


def test_public_surface_exports_new_agent_layers():
    assert hasattr(arf, "AgentDefinition")
    assert hasattr(arf, "AgentRegistry")
    assert hasattr(arf, "SkillAttachment")
    assert hasattr(arf, "McpServiceRef")
