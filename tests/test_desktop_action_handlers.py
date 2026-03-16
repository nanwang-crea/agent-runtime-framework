from __future__ import annotations

from pathlib import Path

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository, ResourceRef
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.applications.desktop_actions import DesktopActionHandlerRegistry


def _context(workspace: Path) -> ApplicationContext:
    return ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        session_memory=InMemorySessionMemory(),
        index_memory=InMemoryIndexMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace)},
    )


def test_handler_registry_executes_summarize_with_preview_mode(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "summary.md"
    file_path.write_text("第一段\n第二段\n第三段", encoding="utf-8")
    registry = DesktopActionHandlerRegistry.default()

    outcome = registry.execute(
        "summarize",
        resources=[ResourceRef.for_path(file_path)],
        context=_context(workspace),
        execution_mode="preview",
    )

    assert outcome["text"] == "第一段"


def test_handler_registry_allows_custom_handler_override(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = DesktopActionHandlerRegistry.default()
    registry.register("summarize", lambda resources, context, execution_mode: {"kind": "summarize", "focused_resources": resources, "text": "custom"})

    outcome = registry.execute(
        "summarize",
        resources=[],
        context=_context(workspace),
        execution_mode="full",
    )

    assert outcome["text"] == "custom"
