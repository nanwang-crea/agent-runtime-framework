from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.last_kwargs = None

    def create(self, **_kwargs):
        self.last_kwargs = _kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


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
        StaticMCPProvider.tools(
            [
                {
                    "name": "external_search",
                    "description": "External search",
                    "runner": lambda user_input, context, session: "mcp:search",
                }
            ]
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
        StaticMCPProvider.tools(
            [
                {
                    "name": "external_search",
                    "description": "External search",
                    "runner": lambda user_input, context, session: "mcp:search",
                }
            ]
        )
    )
    context.services["capability_selector"] = lambda user_input, session, registry, _context: "mcp:external_search"

    result = AgentLoop(context).run("search web")

    assert result.final_answer == "mcp:search"
    assert result.capability_name == "mcp:external_search"


def test_skill_registry_stores_metadata_for_future_planning(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _assistant_context(workspace)

    context.skills.register(
        "report_skill",
        "Builds reports",
        trigger_phrases=["report", "summary"],
        required_capabilities=["desktop_content"],
        planner_hint="Use after collecting files.",
    )

    spec = context.skills.get("report_skill")

    assert spec is not None
    assert spec.trigger_phrases == ["report", "summary"]
    assert spec.required_capabilities == ["desktop_content"]
    assert spec.planner_hint == "Use after collecting files."


def test_agent_loop_uses_llm_first_capability_selector_when_available(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _assistant_context(workspace)
    context.skills.register("hello_skill", "Says hello", runner=lambda user_input, context, session: "skill:hello")
    context.capabilities.register_skill_registry(context.skills)
    context.application_context.llm_client = _FakeLLM(
        '{"capability_name":"skill:hello_skill"}'
    )

    result = AgentLoop(context).run("say hi")

    assert result.final_answer == "skill:hello"
    assert result.capability_name == "skill:hello_skill"


def test_agent_loop_falls_back_to_triggered_skill_when_llm_not_available(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _assistant_context(workspace)
    context.skills.register(
        "report_skill",
        "Builds reports",
        runner=lambda user_input, context, session: "skill:report",
        trigger_phrases=["report"],
    )
    context.capabilities.register_skill_registry(context.skills)

    result = AgentLoop(context).run("please create a report")

    assert result.final_answer == "skill:report"
    assert result.capability_name == "skill:report_skill"


def test_capability_registry_preserves_skill_metadata_for_selector(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _assistant_context(workspace)
    context.skills.register(
        "report_skill",
        "Builds reports",
        runner=lambda user_input, context, session: "skill:report",
        trigger_phrases=["report"],
        required_capabilities=["desktop_content"],
        planner_hint="Use after collecting files.",
    )
    context.capabilities.register_skill_registry(context.skills)

    capability = context.capabilities.require("skill:report_skill")

    assert capability.description == "Builds reports"
    assert capability.safety_level == "skill"
    assert capability.input_contract == {"trigger_phrases": ["report"]}


def test_capability_registry_registers_discoverable_mcp_tools(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _assistant_context(workspace)
    context.capabilities.register_mcp_provider(
        StaticMCPProvider.tools(
            [
                {
                    "name": "external_search",
                    "description": "Search external sources",
                    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
                    "safety_level": "network",
                    "runner": lambda user_input, context, session: "mcp:search",
                }
            ]
        )
    )

    capability = context.capabilities.require("mcp:external_search")

    assert capability.description == "Search external sources"
    assert capability.safety_level == "network"
    assert capability.input_contract == {"type": "object", "properties": {"query": {"type": "string"}}}


def test_agent_loop_llm_selector_prompt_includes_capability_metadata(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _assistant_context(workspace)
    context.skills.register(
        "report_skill",
        "Builds reports",
        runner=lambda user_input, context, session: "skill:report",
        trigger_phrases=["report"],
    )
    context.capabilities.register_skill_registry(context.skills)
    llm = _FakeLLM('{"capability_name":"skill:report_skill"}')
    context.application_context.llm_client = llm

    AgentLoop(context).run("please create a report")

    prompt = llm.completions.last_kwargs["messages"][1]["content"]
    assert "Builds reports" in prompt
    assert "trigger_phrases" in prompt
