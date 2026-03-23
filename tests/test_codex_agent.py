from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime_framework.agents.codex import (
    CodexAction,
    CodexActionResult,
    CodexAgentLoop,
    CodexContext,
    CodexTask,
    VerificationResult,
    build_default_codex_tools,
)
from agent_runtime_framework.agents.codex.planner import _plan_from_goal
from agent_runtime_framework.core.errors import AppError
from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.artifacts import InMemoryArtifactStore
from agent_runtime_framework.assistant import AssistantSession
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.tools import ToolRegistry


class _SequenceCompletions:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._contents.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _SequenceLLM:
    def __init__(self, contents: list[str]) -> None:
        self.completions = _SequenceCompletions(contents)
        self.chat = SimpleNamespace(completions=self.completions)


def _context(workspace: Path) -> CodexContext:
    app_context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace]),
        session_memory=InMemorySessionMemory(),
        index_memory=InMemoryIndexMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace)},
    )
    return CodexContext(application_context=app_context, session=AssistantSession(session_id="codex"))


def _attach_test_next_action_planner(context: CodexContext) -> CodexContext:
    def _planner(task, _session, _context, tool_names):
        completed = [action for action in task.actions if action.status == "completed"]
        if completed:
            last_action = completed[-1]
            if last_action.kind == "respond":
                return None
            if last_action.observation:
                return CodexAction(
                    kind="respond",
                    instruction=last_action.observation,
                    metadata={"direct_output": True},
                )
            return None
        return _plan_from_goal(task.goal, tool_names=set(tool_names))

    context.services["next_action_planner"] = _planner
    return context


def test_codex_models_track_defaults_and_verification(tmp_path: Path):
    context = _context(tmp_path / "workspace")
    context.application_context.resource_repository.allowed_roots[0].mkdir(parents=True, exist_ok=True)

    action = CodexAction(kind="run_command", instruction="pytest")
    verification = VerificationResult(success=True, summary="ok", evidence=["pytest passed"])
    task = CodexTask(goal="Run tests", actions=[action], verification=verification)
    result = CodexActionResult(
        status="completed",
        final_output="done",
        artifacts=[{"artifact_type": "command_log", "title": "pytest", "content": "PASS"}],
    )

    assert action.status == "pending"
    assert task.status == "pending"
    assert task.verification is verification
    assert result.artifacts[0]["artifact_type"] == "command_log"


def test_codex_loop_executes_planned_actions_in_order(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)

    executed: list[str] = []
    context.services["action_planner"] = lambda user_input, session, ctx: [
        {"kind": "read_resource", "instruction": "README.md"},
        {"kind": "respond", "instruction": "all set"},
    ]

    def _executor(action, session, ctx):
        executed.append(action.kind)
        return {"status": "completed", "final_output": f"ran:{action.kind}" if action.kind != "respond" else action.instruction}

    context.services["action_executor"] = _executor

    result = CodexAgentLoop(context).run("inspect repo")

    assert result.status == "completed"
    assert result.final_output == "all set"
    assert executed == ["read_resource", "respond"]


def test_codex_loop_falls_back_to_single_respond_action(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _attach_test_next_action_planner(_context(workspace))

    result = CodexAgentLoop(context).run("hello")

    assert result.status == "completed"
    assert "你好" in result.final_output
    assert result.task.actions[0].kind == "respond"


def test_codex_loop_persists_inline_artifacts(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    store = InMemoryArtifactStore()
    context.services["artifact_store"] = store
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(kind="run_command", instruction="pytest")
    ]
    context.services["action_executor"] = lambda action, session, ctx: CodexActionResult(
        status="completed",
        final_output="pytest passed",
        artifacts=[{"artifact_type": "command_log", "title": "pytest", "content": "PASS"}] if action.kind == "run_command" else [],
    )

    result = CodexAgentLoop(context).run("run tests")

    records = store.list_recent(limit=10, artifact_type="command_log")
    assert result.status == "completed"
    assert len(result.task.artifact_ids) == 1
    assert len(records) == 1
    assert records[0].content == "PASS"


def test_codex_loop_pauses_and_resumes_after_approval(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(kind="apply_patch", instruction="update file", risk_class="high"),
        CodexAction(kind="respond", instruction="done"),
    ]
    context.services["action_executor"] = lambda action, session, ctx: {
        "status": "completed",
        "final_output": action.instruction,
    }
    loop = CodexAgentLoop(context)

    first = loop.run("patch file")

    assert first.status == "needs_approval"
    assert first.resume_token is not None
    assert first.approval_request is not None

    resumed = loop.resume(first.resume_token, approved=True)

    assert resumed.status == "completed"
    assert resumed.final_output == "done"
    assert resumed.task.actions[0].status == "completed"


def test_codex_loop_reads_workspace_file_via_default_tooling(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("hello from codex agent", encoding="utf-8")
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("读取 note.md")

    assert result.status == "completed"
    assert result.action_kind == "respond"
    assert result.final_output == "hello from codex agent"
    assert [action.kind for action in result.task.actions] == ["call_tool", "respond"]


def test_codex_loop_runs_verification_command_and_records_success(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("运行验证 echo ok")

    assert result.status == "completed"
    assert result.action_kind == "respond"
    assert result.task.verification is not None
    assert result.task.verification.success is True
    assert "ok" in result.final_output
    assert [action.kind for action in result.task.actions] == ["run_verification", "respond"]


def test_codex_loop_applies_text_patch_via_default_tooling(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.md"
    target.write_text("hello old world", encoding="utf-8")
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    loop = CodexAgentLoop(context)

    pending = loop.run('把 note.md 里的 "old" 替换成 "new"')

    assert pending.status == "needs_approval"
    assert pending.action_kind == "apply_patch"
    assert pending.resume_token is not None

    result = loop.resume(pending.resume_token, approved=True)

    assert result.status == "completed"
    assert target.read_text(encoding="utf-8") == "hello new world"
    assert "hello new world" in result.final_output


def test_codex_loop_moves_workspace_file_via_default_tooling(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "old.txt"
    source.write_text("move me", encoding="utf-8")
    destination = workspace / "new.txt"
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    loop = CodexAgentLoop(context)

    pending = loop.run("把 old.txt 移动到 new.txt")

    assert pending.status == "needs_approval"
    assert pending.action_kind == "move_path"
    assert pending.resume_token is not None

    result = loop.resume(pending.resume_token, approved=True)

    assert result.status == "completed"
    assert not source.exists()
    assert destination.exists()
    assert "new.txt" in result.final_output


def test_codex_loop_deletes_workspace_file_via_default_tooling(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "trash.txt"
    target.write_text("delete me", encoding="utf-8")
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    loop = CodexAgentLoop(context)

    pending = loop.run("删除 trash.txt")

    assert pending.status == "needs_approval"
    assert pending.action_kind == "delete_path"
    assert pending.resume_token is not None

    result = loop.resume(pending.resume_token, approved=True)

    assert result.status == "completed"
    assert not target.exists()
    assert "trash.txt" in result.final_output


def test_codex_loop_uses_llm_next_action_planner_with_tool_context(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("hello llm planner", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(
        [
            '{"kind":"call_tool","tool_name":"read_workspace_text","arguments":{"path":"note.md"}}',
        ]
    )
    context.application_context.llm_client = llm

    result = CodexAgentLoop(context).run("please inspect the note for me")

    assert result.status == "completed"
    assert result.final_output == "hello llm planner"
    assert [action.kind for action in result.task.actions] == ["call_tool", "respond"]
    assert len(llm.completions.calls) == 1
    first_prompt = llm.completions.calls[0]["messages"][-1]["content"]
    system_prompt = llm.completions.calls[0]["messages"][0]["content"]
    assert "read_workspace_text" in first_prompt
    assert "kind 只能是" in system_prompt
    assert "run_shell_command" in first_prompt
    assert '"kind":"call_tool"' in first_prompt


def test_codex_loop_creates_workspace_file_via_default_tooling(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "draft.txt"
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    loop = CodexAgentLoop(context)

    pending = loop.run("创建 draft.txt 内容 hello world")

    assert pending.status == "needs_approval"
    assert pending.action_kind == "create_path"
    assert pending.resume_token is not None

    result = loop.resume(pending.resume_token, approved=True)

    assert result.status == "completed"
    assert target.read_text(encoding="utf-8") == "hello world"
    assert "hello world" in result.final_output


def test_codex_loop_edits_workspace_file_via_default_tooling(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "draft.txt"
    target.write_text("old text", encoding="utf-8")
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    loop = CodexAgentLoop(context)

    pending = loop.run("编辑 draft.txt 内容 new text")

    assert pending.status == "needs_approval"
    assert pending.action_kind == "edit_text"
    assert pending.resume_token is not None

    result = loop.resume(pending.resume_token, approved=True)

    assert result.status == "completed"
    assert target.read_text(encoding="utf-8") == "new text"
    assert "new text" in result.final_output


def test_codex_loop_surfaces_planner_runtime_missing(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)

    with pytest.raises(AppError) as exc_info:
        CodexAgentLoop(context).run("读取 note.md")

    assert exc_info.value.code == "PLANNER_RUNTIME_MISSING"


def test_codex_loop_surfaces_planner_invalid_json(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("hello", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(["not json"])
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    with pytest.raises(AppError) as exc_info:
        CodexAgentLoop(context).run("读取 note.md")

    assert exc_info.value.code == "PLANNER_INVALID_JSON"


def test_codex_loop_surfaces_planner_normalization_failed(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(['{"kind":"call_tool","tool_name":"missing_tool","arguments":{}}'])
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    with pytest.raises(AppError) as exc_info:
        CodexAgentLoop(context).run("读取 note.md")

    assert exc_info.value.code == "PLANNER_NORMALIZATION_FAILED"
    assert "tool_name 'missing_tool' is not in available tools" in exc_info.value.detail
    assert '"tool_name": "missing_tool"' in exc_info.value.detail
