from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime_framework.applications import (
    ApplicationContext,
    ApplicationRunner,
    create_desktop_content_application,
)
from agent_runtime_framework.artifacts import FileArtifactStore, InMemoryArtifactStore
from agent_runtime_framework.core.errors import AppError
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository, ResourceRef
from agent_runtime_framework.tools import ToolRegistry


def _build_context(workspace: Path) -> ApplicationContext:
    return ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        session_memory=InMemorySessionMemory(),
        index_memory=InMemoryIndexMemory(),
        artifact_store=InMemoryArtifactStore(),
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
    assert "下面一共有" in result.final_answer
    assert "文件：" in result.final_answer
    assert "a.md" in result.final_answer
    assert "b.txt" in result.final_answer


def test_desktop_application_lists_named_subdirectory_instead_of_root(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "root.txt").write_text("root", encoding="utf-8")
    target_dir = workspace / "agent_runtime_framework"
    target_dir.mkdir()
    (target_dir / "sub.py").write_text("print('ok')", encoding="utf-8")

    app = create_desktop_content_application()
    runner = ApplicationRunner(app, _build_context(workspace))

    result = runner.run("可以给我列一下 agent_runtime_framework 下面都有哪些文件吗？")

    assert result.status == "completed"
    assert "sub.py" in result.final_answer
    assert "root.txt" not in result.final_answer


def test_desktop_application_uses_focused_directory_for_follow_up_list(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target_dir = workspace / "docs"
    target_dir.mkdir()
    (target_dir / "a.md").write_text("a", encoding="utf-8")
    (workspace / "root.txt").write_text("root", encoding="utf-8")

    app = create_desktop_content_application()
    context = _build_context(workspace)
    runner = ApplicationRunner(app, context)

    first = runner.run("列一下 docs 下面都有哪些文件")
    second = runner.run("再列一下下面的文件")

    assert first.status == "completed"
    assert second.status == "completed"
    assert "a.md" in second.final_answer
    assert "root.txt" not in second.final_answer


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


def test_desktop_application_uses_custom_interpreter_without_llm(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.md").write_text("hello", encoding="utf-8")

    context = _build_context(workspace)
    context.services["intent_parser"] = lambda user_input, _context: {
        "action": "list",
        "target_name": None,
        "use_last_focus": False,
    }
    app = create_desktop_content_application()
    runner = ApplicationRunner(app, context)

    result = runner.run("列出当前目录")

    assert result.status == "completed"
    assert "a.md" in result.final_answer


def test_desktop_application_create_file_requires_confirmation_then_executes(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_desktop_content_application()
    context = _build_context(workspace)
    runner = ApplicationRunner(app, context)

    pending = runner.run("创建 note.txt 内容 hello")

    assert pending.status == "requires_confirmation"
    assert "note.txt" in pending.final_answer
    assert "+hello" in pending.final_answer
    assert not (workspace / "note.txt").exists()

    completed = runner.run("创建 note.txt 内容 hello", confirmed=True)

    assert completed.status == "completed"
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "hello"


def test_desktop_application_create_directory_requires_confirmation_then_executes(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_desktop_content_application()
    context = _build_context(workspace)
    runner = ApplicationRunner(app, context)

    pending = runner.run("创建文件夹 docs")

    assert pending.status == "requires_confirmation"
    assert "mkdir" in pending.final_answer

    completed = runner.run("创建文件夹 docs", confirmed=True)

    assert completed.status == "completed"
    assert (workspace / "docs").is_dir()


def test_desktop_application_edit_move_delete_with_confirmation_and_artifacts(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "note.txt"
    file_path.write_text("old", encoding="utf-8")
    app = create_desktop_content_application()
    context = _build_context(workspace)
    runner = ApplicationRunner(app, context)

    preview = runner.run("编辑 note.txt 内容 new")
    assert preview.status == "requires_confirmation"
    assert "-old" in preview.final_answer
    assert "+new" in preview.final_answer

    edited = runner.run("编辑 note.txt 内容 new", confirmed=True)
    assert edited.status == "completed"
    assert file_path.read_text(encoding="utf-8") == "new"

    moved_preview = runner.run("移动 note.txt 到 archive.txt")
    assert moved_preview.status == "requires_confirmation"
    moved = runner.run("移动 note.txt 到 archive.txt", confirmed=True)
    assert moved.status == "completed"
    assert not (workspace / "note.txt").exists()
    assert (workspace / "archive.txt").exists()

    delete_preview = runner.run("删除 archive.txt")
    assert delete_preview.status == "requires_confirmation"
    deleted = runner.run("删除 archive.txt", confirmed=True)
    assert deleted.status == "completed"
    assert not (workspace / "archive.txt").exists()

    artifacts = context.artifact_store.list_recent(limit=10)
    assert any(item.artifact_type == "change_summary" for item in artifacts)


def test_desktop_application_rolls_back_edit_when_post_apply_fails(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "note.txt"
    file_path.write_text("old", encoding="utf-8")
    app = create_desktop_content_application()
    context = _build_context(workspace)
    context.services["mutation_fail_after_apply"] = "edit"
    runner = ApplicationRunner(app, context)

    runner.run("编辑 note.txt 内容 new")

    with pytest.raises(AppError) as exc_info:
        runner.run("编辑 note.txt 内容 new", confirmed=True)

    assert exc_info.value.code == "MUTATION_EXECUTION_FAILED"
    assert file_path.read_text(encoding="utf-8") == "old"
    artifacts = context.artifact_store.list_recent(limit=10)
    assert any(item.artifact_type == "rollback_checkpoint" for item in artifacts)


def test_desktop_application_rolls_back_completed_actions_when_later_action_fails(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_desktop_content_application()
    context = _build_context(workspace)
    context.services["planner_parser"] = lambda intent, resources, _context: {"actions": ["create", "move"]}
    context.services["mutation_fail_after_apply"] = "move"
    runner = ApplicationRunner(app, context)

    with pytest.raises(AppError) as exc_info:
        runner.run("移动 note.txt 到 archive.txt", confirmed=True)

    assert exc_info.value.code == "MUTATION_EXECUTION_FAILED"
    assert not (workspace / "note.txt").exists()
    artifacts = context.artifact_store.list_recent(limit=10)
    assert any(item.artifact_type == "rollback_summary" for item in artifacts)


def test_file_artifact_store_persists_change_summary_to_disk(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact_dir = workspace / ".arf" / "artifacts"
    app = create_desktop_content_application()
    context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        session_memory=InMemorySessionMemory(),
        index_memory=InMemoryIndexMemory(),
        artifact_store=FileArtifactStore(artifact_dir),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace)},
    )
    runner = ApplicationRunner(app, context)

    runner.run("创建 note.txt 内容 hello")
    runner.run("创建 note.txt 内容 hello", confirmed=True)

    reloaded_store = FileArtifactStore(artifact_dir)
    artifacts = reloaded_store.list_recent(limit=20)
    assert any(item.artifact_type == "change_summary" for item in artifacts)
