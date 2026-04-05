import agent_runtime_framework as arf
from agent_runtime_framework import workflow


def test_public_surface_exports_new_agent_layers():
    assert hasattr(arf, "AgentDefinition")
    assert hasattr(arf, "AgentRegistry")
    assert hasattr(arf, "SkillAttachment")
    assert hasattr(arf, "McpServiceRef")


def test_workflow_surface_no_longer_exports_legacy_graph_builder():
    assert not hasattr(workflow, "build_workspace_subtask_graph")
