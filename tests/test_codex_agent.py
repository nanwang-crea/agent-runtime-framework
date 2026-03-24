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
    CodexTaskMemory,
    VerificationResult,
    build_default_codex_tools,
    evaluate_codex_output,
)
from agent_runtime_framework.agents.codex.planner import _plan_from_goal
from agent_runtime_framework.agents.codex.runtime import CodexSessionRuntime
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
    assert task.memory.known_facts == []
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


def test_codex_output_evaluator_promotes_directory_explanation_into_inspect_then_summary(tmp_path: Path):
    workspace = tmp_path / "workspace"
    package = workspace / "agent_runtime_framework"
    assistant = package / "assistant"
    workspace.mkdir()
    package.mkdir()
    assistant.mkdir()
    (package / "__init__.py").write_text('"""Runtime package entry."""\n', encoding="utf-8")
    (assistant / "conversation.py").write_text('"""Conversation helpers."""\n', encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "list_workspace_directory", "arguments": {"path": "agent_runtime_framework"}},
        )
    ]

    result = CodexAgentLoop(context).run("介绍 agent_runtime_framework 目录结构和功能")

    assert result.status == "completed"
    assert [action.kind for action in result.task.actions] == ["call_tool", "call_tool", "respond"]
    assert result.task.actions[1].metadata["tool_name"] == "inspect_workspace_path"
    assert "assistant/" in result.final_output
    assert "__init__.py" in result.final_output


def test_codex_output_evaluator_synthesizes_read_content_for_summary_requests(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("alpha\nbeta\ngamma\ndelta", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "read_workspace_text", "arguments": {"path": "note.md"}},
        )
    ]

    result = CodexAgentLoop(context).run("总结 note.md 主要内容")

    assert result.status == "completed"
    assert [action.kind for action in result.task.actions] == ["call_tool", "respond"]
    assert "我先基于已读取内容做一个简要说明" in result.final_output
    assert "- alpha" in result.final_output


def test_codex_output_evaluator_prefers_llm_decision_when_available(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("alpha\nbeta\ngamma\ndelta", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "read_workspace_text", "arguments": {"path": "note.md"}},
        )
    ]
    llm = _SequenceLLM([
        '{"decision":"continue","kind":"respond","instruction":"这是模型判断后的总结。","direct_output":true}',
        '{"decision":"finish"}',
    ])
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    result = CodexAgentLoop(context).run("总结 note.md 主要内容")

    assert result.status == "completed"
    assert result.final_output == "这是模型判断后的总结。"
    assert [action.kind for action in result.task.actions] == ["call_tool", "respond"]
    assert result.task.actions[1].metadata["evaluation_source"] == "model"


def test_codex_output_evaluator_marks_fallback_source_when_model_is_unavailable(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("alpha\nbeta\ngamma\ndelta", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "read_workspace_text", "arguments": {"path": "note.md"}},
        )
    ]

    result = CodexAgentLoop(context).run("总结 note.md 主要内容")

    assert result.status == "completed"
    assert result.task.actions[1].metadata["evaluation_source"] == "fallback"


def test_codex_output_evaluator_blocks_finish_when_open_questions_exist():
    task = CodexTask(
        goal="总结 note.md",
        actions=[
            CodexAction(
                kind="respond",
                instruction="done",
                status="completed",
                metadata={"direct_output": True},
                observation="done",
            )
        ],
        memory=CodexTaskMemory(open_questions=["still missing evidence"]),
    )

    decision = evaluate_codex_output(task, None, SimpleNamespace(application_context=SimpleNamespace(llm_client=None, llm_model="test")), [])

    assert decision.status != "finish"


def test_codex_output_evaluator_blocks_finish_when_verification_is_pending():
    task = CodexTask(
        goal="检查修改是否生效",
        actions=[
            CodexAction(
                kind="respond",
                instruction="done",
                status="completed",
                metadata={"direct_output": True},
                observation="done",
            )
        ],
        memory=CodexTaskMemory(pending_verifications=["pytest -q"]),
    )

    decision = evaluate_codex_output(task, None, SimpleNamespace(application_context=SimpleNamespace(llm_client=None, llm_model="test")), ["run_shell_command"])

    assert decision.status == "continue"
    assert decision.next_action is not None
    assert decision.next_action.kind == "run_verification"


def test_codex_task_memory_tracks_read_and_modified_paths(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction="read note",
            metadata={"tool_name": "read_workspace_text", "arguments": {"path": "note.md"}},
        ),
        CodexAction(
            kind="edit_text",
            instruction="edit note",
            metadata={"tool_name": "edit_workspace_text", "arguments": {"path": "note.md"}},
        ),
        CodexAction(kind="respond", instruction="done", metadata={"direct_output": True}),
    ]

    def _executor(action, session, ctx):
        if action.kind == "call_tool":
            return {
                "status": "completed",
                "final_output": "note body",
                "metadata": {"tool_output": {"path": "note.md", "summary": "note body", "text": "note body"}},
            }
        if action.kind == "edit_text":
            return {
                "status": "completed",
                "final_output": "updated note",
                "metadata": {"tool_output": {"path": "note.md", "changed_paths": ["note.md"], "text": "updated note"}},
            }
        return {"status": "completed", "final_output": action.instruction}

    context.services["action_executor"] = _executor

    result = CodexAgentLoop(context).run("edit note")

    assert result.status == "completed"
    assert result.task.memory.read_paths == ["note.md"]
    assert result.task.memory.modified_paths == ["note.md"]


def test_codex_task_memory_tracks_pending_verifications_until_run(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="edit_text",
            instruction="edit note",
            metadata={"tool_name": "edit_workspace_text", "arguments": {"path": "note.md"}},
        ),
        CodexAction(
            kind="run_verification",
            instruction="pytest -q",
            metadata={"command": "pytest -q"},
        ),
        CodexAction(kind="respond", instruction="done", metadata={"direct_output": True}),
    ]

    def _executor(action, session, ctx):
        if action.kind == "edit_text":
            return {
                "status": "completed",
                "final_output": "updated note",
                "metadata": {"tool_output": {"path": "note.md", "changed_paths": ["note.md"], "text": "updated note"}},
            }
        if action.kind == "run_verification":
            return {
                "status": "completed",
                "final_output": "ok",
                "metadata": {"verification": {"success": True, "summary": "ok", "command": "pytest -q"}},
            }
        return {"status": "completed", "final_output": action.instruction}

    context.services["action_executor"] = _executor

    result = CodexAgentLoop(context).run("edit and verify note")

    assert result.status == "completed"
    assert result.task.memory.pending_verifications == []
    assert "pytest -q" in result.task.memory.known_facts[-1]


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


def test_run_shell_command_uses_sandbox_and_returns_sandbox_state(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "run_shell_command", "arguments": {"command": "pwd"}},
        )
    ]

    result = CodexAgentLoop(context).run("run pwd")

    assert result.status == "completed"
    tool_output = result.task.actions[0].metadata["result"]["tool_output"]
    assert tool_output["sandbox_applied"] is True
    assert tool_output["sandbox"]["mode"] == "workspace_write"


def test_run_shell_command_blocks_shell_metacharacters(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "run_shell_command", "arguments": {"command": "pwd && ls"}},
        )
    ]

    with pytest.raises(AppError) as exc_info:
        CodexAgentLoop(context).run("run dangerous shell")

    assert exc_info.value.code == "SANDBOX_DENIED"


def test_run_shell_command_blocks_network_commands_by_default(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "run_shell_command", "arguments": {"command": "curl https://example.com"}},
        )
    ]

    with pytest.raises(AppError) as exc_info:
        CodexAgentLoop(context).run("fetch remote")

    assert exc_info.value.code == "SANDBOX_DENIED"


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


def test_codex_loop_llm_planner_includes_tool_prompt_metadata(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(['{"kind":"respond","instruction":"ok"}'])
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    result = CodexAgentLoop(context).run("随便看一下")

    assert result.status == "completed"
    planner_prompt = llm.completions.calls[0]["messages"][-1]["content"]
    assert "snippet:" in planner_prompt
    assert "guidelines:" in planner_prompt
    assert "Prefer read_workspace_text over shell cat" in planner_prompt


def test_codex_read_tool_returns_agent_friendly_metadata(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("\n".join(f"line-{index}-content" for index in range(20)), encoding="utf-8")
    context = _context(workspace)
    context.application_context.config["codex_max_read_chars"] = 12
    tool = next(tool for tool in build_default_codex_tools() if tool.name == "read_workspace_text")

    output = tool.executor(None, context, {"path": "note.md"})

    assert output["summary"]
    assert output["truncated"] is True
    assert output["next_hint"]
    assert output["entities"]["path"] == str(workspace / "note.md")


def test_codex_edit_tool_returns_change_summary_metadata(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "draft.txt").write_text("old text", encoding="utf-8")
    context = _context(workspace)
    tool = next(tool for tool in build_default_codex_tools() if tool.name == "edit_workspace_text")

    output = tool.executor(None, context, {"path": "draft.txt", "content": "new text"})

    assert output["summary"]
    assert output["changed_paths"] == ["draft.txt"]
    assert output["next_hint"]


def test_codex_loop_records_runtime_events_and_task_summary(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("\n".join(f"line-{index}" for index in range(40)), encoding="utf-8")
    context = _context(workspace)
    runtime = CodexSessionRuntime(max_observation_chars=80)
    context.services["session_runtime"] = runtime
    context.application_context.config["codex_max_read_chars"] = 80
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "read_workspace_text", "arguments": {"path": "note.md"}},
        )
    ]

    result = CodexAgentLoop(context).run("读取 note.md")

    assert result.status == "completed"
    assert result.task.summary
    assert "read_workspace_text" in result.task.summary
    assert runtime.events
    assert runtime.events[0]["type"] == "task_started"
    assert any(event["type"] == "tool_result" for event in runtime.events)
    assert "输出已截断" in (result.task.actions[0].observation or "")


def test_codex_planner_assigns_subgoal_for_analysis_request(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("alpha\nbeta", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(['{"kind":"call_tool","tool_name":"read_workspace_text","arguments":{"path":"note.md"}}'])
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    result = CodexAgentLoop(context).run("总结 note.md 主要内容")

    assert result.task.actions[0].subgoal in {"gather_evidence", "analyze_target"}


def test_codex_complex_task_advances_across_subgoals(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pkg").mkdir()
    (workspace / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "pkg" / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "list_workspace_directory", "arguments": {"path": "pkg"}},
        )
    ]

    result = CodexAgentLoop(context).run("介绍 pkg 目录结构并总结 service.py 的作用")

    assert result.status == "completed"
    assert len(result.task.actions) >= 3
    subgoals = [action.subgoal for action in result.task.actions]
    assert "gather_evidence" in subgoals
    assert "synthesize_answer" in subgoals
    assert "service.py" in result.final_output


def test_codex_task_memory_extracts_claims_from_inspect_output(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction="inspect pkg",
            metadata={"tool_name": "inspect_workspace_path", "arguments": {"path": "pkg"}},
        ),
        CodexAction(kind="respond", instruction="done", metadata={"direct_output": True}),
    ]

    def _executor(action, session, ctx):
        if action.kind == "call_tool":
            text = "pkg 下面共有 2 个条目。\n关键文件：\n- service.py：定义了 run\n- utils.py：定义了 format_value"
            return {
                "status": "completed",
                "final_output": text,
                "metadata": {"tool_output": {"path": "pkg", "summary": "pkg structure", "text": text}},
            }
        return {"status": "completed", "final_output": action.instruction}

    context.services["action_executor"] = _executor

    result = CodexAgentLoop(context).run("介绍 pkg")

    assert result.status == "completed"
    assert any("service.py" in claim for claim in result.task.memory.claims)
    assert any("utils.py" in claim for claim in result.task.memory.claims)


def test_codex_complex_task_uses_claims_for_role_summary(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pkg").mkdir()
    (workspace / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "pkg" / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "list_workspace_directory", "arguments": {"path": "pkg"}},
        )
    ]

    result = CodexAgentLoop(context).run("介绍 pkg 目录结构并总结 service.py 的作用")

    assert result.status == "completed"
    assert "service.py 的作用" in result.final_output
    assert "定义了 run" in result.final_output


def test_codex_task_memory_stores_typed_claims(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction="inspect pkg",
            metadata={"tool_name": "inspect_workspace_path", "arguments": {"path": "pkg"}},
        ),
        CodexAction(kind="respond", instruction="done", metadata={"direct_output": True}),
    ]

    def _executor(action, session, ctx):
        if action.kind == "call_tool":
            text = "pkg 下面共有 2 个条目。\n关键文件：\n- service.py：定义了 run\n- utils.py：定义了 format_value"
            return {
                "status": "completed",
                "final_output": text,
                "metadata": {"tool_output": {"path": "pkg", "summary": "pkg structure", "text": text}},
            }
        return {"status": "completed", "final_output": action.instruction}

    context.services["action_executor"] = _executor

    result = CodexAgentLoop(context).run("介绍 pkg")

    assert result.status == "completed"
    assert any(claim["kind"] == "role" for claim in result.task.memory.typed_claims)
    assert any(claim["subject"] == "service.py" for claim in result.task.memory.typed_claims)


def test_codex_complex_task_combines_structure_and_role_claims(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pkg").mkdir()
    (workspace / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "pkg" / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    (workspace / "pkg" / "utils.py").write_text("def format_value(value):\n    return str(value).upper()\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output
    context.services["action_planner"] = lambda user_input, session, ctx: [
        CodexAction(
            kind="call_tool",
            instruction=user_input,
            metadata={"tool_name": "list_workspace_directory", "arguments": {"path": "pkg"}},
        )
    ]

    result = CodexAgentLoop(context).run("介绍 pkg 目录结构并总结 service.py 的作用")

    assert result.status == "completed"
    assert "目录结构" in result.final_output
    assert "service.py 的作用" in result.final_output
    assert "utils.py" in result.final_output


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
