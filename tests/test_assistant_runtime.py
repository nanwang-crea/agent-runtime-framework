from __future__ import annotations

from pathlib import Path

from agent_runtime_framework.applications import ApplicationContext, create_desktop_content_application
from agent_runtime_framework.assistant import (
    AgentLoop,
    AssistantContext,
    AssistantSession,
    CapabilityRegistry,
    SkillRegistry,
    StaticMCPProvider,
)
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.tools import ToolRegistry


def _assistant_context(workspace: Path) -> AssistantContext:
    app_context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        session_memory=InMemorySessionMemory(),
        index_memory=InMemoryIndexMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace)},
    )
    return AssistantContext(
        application_context=app_context,
        capabilities=CapabilityRegistry(),
        skills=SkillRegistry(),
    )


def test_assistant_session_tracks_turns():
    session = AssistantSession(session_id="demo")

    session.add_turn("user", "hello")
    session.add_turn("assistant", "world")

    assert len(session.turns) == 2
    assert session.turns[-1].content == "world"


def test_capability_registry_collects_local_skill_and_mcp_capabilities(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _assistant_context(workspace)
    registry = context.capabilities

    registry.register_application("desktop_content", create_desktop_content_application())
    context.skills.register("summarizer_skill", "Skill summary")
    registry.register_skill_registry(context.skills)
    registry.register_mcp_provider(
        StaticMCPProvider(
            {
                "external_search": lambda user_input, context, session: "mcp:search",
            }
        )
    )

    assert "desktop_content" in registry.names()
    assert "skill:summarizer_skill" in registry.names()
    assert "mcp:external_search" in registry.names()


def test_agent_loop_invokes_desktop_content_capability(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("hello desktop", encoding="utf-8")
    context = _assistant_context(workspace)
    context.capabilities.register_application("desktop_content", create_desktop_content_application())
    context.services["capability_selector"] = lambda user_input, session, registry, _context: "desktop_content"

    result = AgentLoop(context).run("读取 note.md")

    assert result.final_answer == "hello desktop"
    assert result.capability_name == "desktop_content"


def test_agent_loop_invokes_skill_capability_when_selected(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _assistant_context(workspace)
    context.skills.register("hello_skill", "Says hello", runner=lambda user_input, context, session: "skill:hello")
    context.capabilities.register_skill_registry(context.skills)
    context.services["capability_selector"] = lambda user_input, session, registry, _context: "skill:hello_skill"

    result = AgentLoop(context).run("say hi")

    assert result.final_answer == "skill:hello"
    assert result.capability_name == "skill:hello_skill"


def test_agent_loop_invokes_mcp_capability_when_selected(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _assistant_context(workspace)
    context.capabilities.register_mcp_provider(
        StaticMCPProvider(
            {
                "external_search": lambda user_input, context, session: "mcp:search",
            }
        )
    )
    context.services["capability_selector"] = lambda user_input, session, registry, _context: "mcp:external_search"

    result = AgentLoop(context).run("search web")

    assert result.final_answer == "mcp:search"
    assert result.capability_name == "mcp:external_search"
