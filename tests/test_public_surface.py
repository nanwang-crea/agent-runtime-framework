import agent_runtime_framework as arf
from agent_runtime_framework import workflow
from agent_runtime_framework.api.app import create_app
from pathlib import Path


def test_public_surface_exports_new_agent_layers():
    assert hasattr(arf, "SkillAttachment")
    assert hasattr(arf, "MemoryManager")
    assert hasattr(arf, "McpServiceRef")
    assert arf.create_app is create_app


def test_workflow_surface_no_longer_exports_legacy_graph_builder():
    assert not hasattr(workflow, "build_workspace_subtask_graph")
    assert not hasattr(workflow, "WorkspaceSubtaskExecutor")


def test_workspace_write_node_architecture_note_defines_public_taxonomy():
    note = Path("docs/architecture/workspace-write-nodes.md")

    assert note.exists()

    content = note.read_text(encoding="utf-8")

    for node_name in (
        "create_path",
        "move_path",
        "delete_path",
        "apply_patch",
        "write_file",
        "append_text",
        "verification",
    ):
        assert f"`{node_name}`" in content

    assert "`workspace_subtask` is being removed, not expanded" in content
