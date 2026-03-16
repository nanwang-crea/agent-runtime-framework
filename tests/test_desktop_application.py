from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_runtime_framework.applications import (
    ApplicationContext,
    ApplicationRunner,
    create_desktop_content_application,
)
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository, ResourceRef
from agent_runtime_framework.tools import ToolRegistry


def _build_context(workspace: Path) -> ApplicationContext:
    return ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        session_memory=InMemorySessionMemory(),
        index_memory=InMemoryIndexMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace)},
    )


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    def create(self, **_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self._content),
                )
            ]
        )


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(content))


def test_desktop_application_lists_directory(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.md").write_text("hello", encoding="utf-8")
    (workspace / "b.txt").write_text("world", encoding="utf-8")

    app = create_desktop_content_application()
    runner = ApplicationRunner(app, _build_context(workspace))

    result = runner.run("列出当前目录")

    assert result.status == "completed"
    assert "a.md" in result.final_answer
    assert "b.txt" in result.final_answer


def test_desktop_application_reads_file_after_follow_up_reference(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "note.md"
    file_path.write_text("hello desktop assistant", encoding="utf-8")

    app = create_desktop_content_application()
    context = _build_context(workspace)
    runner = ApplicationRunner(app, context)

    first = runner.run("读取 note.md")
    second = runner.run("再看刚才那个文件")

    assert first.status == "completed"
    assert second.status == "completed"
    assert "hello desktop assistant" in second.final_answer
    assert context.session_memory.snapshot().focused_resources == [ResourceRef.for_path(file_path)]


def test_desktop_application_summarizes_document(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "summary.md"
    file_path.write_text("第一段\n第二段\n第三段", encoding="utf-8")

    app = create_desktop_content_application()
    runner = ApplicationRunner(app, _build_context(workspace))

    result = runner.run("总结 summary.md")

    assert result.status == "completed"
    assert "第一段" in result.final_answer
    assert "第三段" in result.final_answer


def test_desktop_application_prefers_llm_interpretation_when_available(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "summary.md"
    file_path.write_text("第一段\n第二段\n第三段", encoding="utf-8")

    context = _build_context(workspace)
    context.llm_client = _FakeLLM(
        '{"action":"summarize","target_name":"summary.md","use_last_focus":false}'
    )
    app = create_desktop_content_application()
    runner = ApplicationRunner(app, context)

    result = runner.run("帮我概括一下 summary.md")

    assert result.status == "completed"
    assert "第一段" in result.final_answer


def test_desktop_application_uses_custom_interpreter_when_provided(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "summary.md"
    file_path.write_text("第一段\n第二段\n第三段", encoding="utf-8")

    context = _build_context(workspace)
    context.services["intent_parser"] = lambda user_input, _context: {
        "action": "summarize",
        "target_name": "summary.md",
        "use_last_focus": False,
    }
    app = create_desktop_content_application()
    runner = ApplicationRunner(app, context)

    result = runner.run("随便怎么说都行")

    assert result.status == "completed"
    assert "第一段" in result.final_answer


def test_desktop_application_uses_custom_resolver_parser_when_provided(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "summary.md").write_text("第一段\n第二段\n第三段", encoding="utf-8")

    context = _build_context(workspace)
    context.services["intent_parser"] = lambda user_input, _context: {
        "action": "summarize",
        "target_name": None,
        "use_last_focus": False,
    }
    context.services["resolver_parser"] = lambda intent, snapshot, default_directory, _context: {
        "target_name": "summary.md",
        "use_last_focus": False,
        "use_default_directory": False,
    }
    app = create_desktop_content_application()
    runner = ApplicationRunner(app, context)

    result = runner.run("帮我处理一下")

    assert result.status == "completed"
    assert "第一段" in result.final_answer


def test_desktop_application_uses_custom_planner_parser_when_provided(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "summary.md").write_text("第一段\n第二段\n第三段", encoding="utf-8")

    context = _build_context(workspace)
    context.services["intent_parser"] = lambda user_input, _context: {
        "action": "read",
        "target_name": "summary.md",
        "use_last_focus": False,
    }
    context.services["planner_parser"] = lambda intent, resources, _context: {
        "actions": ["summarize"]
    }
    app = create_desktop_content_application()
    runner = ApplicationRunner(app, context)

    result = runner.run("读取 summary.md")

    assert result.status == "completed"
    assert result.final_answer == "第一段\n第二段\n第三段"


def test_desktop_application_uses_custom_executor_parser_when_provided(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "summary.md").write_text("第一段\n第二段\n第三段", encoding="utf-8")

    context = _build_context(workspace)
    context.services["intent_parser"] = lambda user_input, _context: {
        "action": "summarize",
        "target_name": "summary.md",
        "use_last_focus": False,
    }
    context.services["executor_parser"] = lambda action, _context: {
        "mode": "preview"
    }
    app = create_desktop_content_application()
    runner = ApplicationRunner(app, context)

    result = runner.run("总结 summary.md")

    assert result.status == "completed"
    assert result.final_answer == "第一段"


def test_desktop_application_uses_custom_composer_parser_when_provided(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "summary.md").write_text("第一段\n第二段\n第三段", encoding="utf-8")

    context = _build_context(workspace)
    context.services["composer_parser"] = lambda outcome, _context: {
        "text": f"整理结果：{outcome['text']}"
    }
    app = create_desktop_content_application()
    runner = ApplicationRunner(app, context)

    result = runner.run("总结 summary.md")

    assert result.status == "completed"
    assert result.final_answer.startswith("整理结果：")


def test_desktop_application_uses_custom_action_handler_registry_when_provided(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "summary.md").write_text("第一段\n第二段\n第三段", encoding="utf-8")

    class CustomRegistry:
        def execute(self, action_name, resources, context, execution_mode):
            return {
                "kind": action_name,
                "focused_resources": resources,
                "text": f"handled:{action_name}:{execution_mode}",
            }

    context = _build_context(workspace)
    context.services["action_handler_registry"] = CustomRegistry()
    app = create_desktop_content_application()
    runner = ApplicationRunner(app, context)

    result = runner.run("总结 summary.md")

    assert result.status == "completed"
    assert result.final_answer == "handled:summarize:full"


def test_desktop_application_falls_back_to_rule_interpretation_without_llm(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.md").write_text("hello", encoding="utf-8")

    app = create_desktop_content_application()
    runner = ApplicationRunner(app, _build_context(workspace))

    result = runner.run("列出当前目录")

    assert result.status == "completed"
    assert "a.md" in result.final_answer
