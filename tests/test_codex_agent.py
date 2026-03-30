from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime_framework.agents.codex import (
    CodexAction,
    CodexActionResult,
    CodexAgentLoop,
    CodexPlan,
    CodexPlanTask,
    CodexContext,
    CodexTask,
    CodexTaskMemory,
    VerificationResult,
    build_default_codex_tools,
    evaluate_codex_output,
)
from agent_runtime_framework.agents.codex.planner import _plan_from_goal
from agent_runtime_framework.agents.codex.prompting import build_codex_system_prompt
from agent_runtime_framework.agents.codex.profiles import classify_task_profile
from agent_runtime_framework.agents.codex.run_context import available_tool_names, build_run_context
from agent_runtime_framework.agents.codex.semantics import infer_task_intent
from agent_runtime_framework.agents.codex.workflows import WorkflowRegistry
from agent_runtime_framework.agents.codex.runtime import CodexSessionRuntime
from agent_runtime_framework.agents.codex.task_plans import (
    advance_task_plan,
    attach_action_to_plan,
    build_task_plan,
    plan_next_task_action,
)
from agent_runtime_framework.core.errors import AppError
from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.artifacts import InMemoryArtifactStore
from agent_runtime_framework.assistant import AssistantSession
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory, MemoryRecord
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository, ResourceRef
from agent_runtime_framework.tools.executor import execute_tool_call
from agent_runtime_framework.tools.models import ToolCall
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


def _benchmark_context(workspace: Path, *, llm_contents: list[str] | None = None) -> tuple[CodexContext, _SequenceLLM | None]:
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output
    context.services["model_first_task_profile_classifier"] = True
    llm = _SequenceLLM(llm_contents or []) if llm_contents is not None else None
    if llm is not None:
        context.application_context.llm_client = llm
        context.application_context.llm_model = "test-model"
    return context, llm


def _score_workspace_question(result, *, expected_profile: str, expected_refs: list[str], expected_tokens: list[str]) -> int:
    score = 0
    if result.status == "completed":
        score += 1
    if result.task.task_profile == expected_profile:
        score += 1
    if any(action.metadata.get("tool_name") == "resolve_workspace_target" for action in result.task.actions):
        score += 1
    if all(token in result.final_output for token in expected_tokens):
        score += 1
    if "引用：" in result.final_output and all(ref in result.final_output for ref in expected_refs):
        score += 1
    return score


def _tool_trace(result) -> list[str]:
    return [str(action.metadata.get("tool_name") or "") for action in result.task.actions if action.kind == "call_tool"]


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


def test_infer_task_intent_detects_directory_explanation_variant():
    intent = infer_task_intent("能不能带我看看 agent_runtime_framework 这个文件夹的职责分布")

    assert intent.task_kind == "repository_explainer"
    assert intent.user_intent == "explain_directory"
    assert intent.target_hint == "agent_runtime_framework"
    assert intent.target_type == "directory"
    assert "inspect_workspace_path" in intent.suggested_tool_chain


def test_classify_task_profile_uses_semantic_fallback_without_model(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)

    profile = classify_task_profile("帮我梳理一下 src 目录的作用和结构", context)

    assert profile == "repository_explainer"


def test_codex_models_support_task_level_plan_defaults():
    plan = CodexPlan(tasks=[CodexPlanTask(title="List target", kind="list_target")])
    task = CodexTask(goal="Explain pkg", actions=[], task_profile="repository_explainer", plan=plan)

    assert task.plan is plan
    assert task.plan.tasks[0].status == "pending"
    assert task.plan.tasks[0].action_indexes == []


def test_codex_system_prompt_loads_external_markdown_sections():
    prompt = build_codex_system_prompt("你负责做 planner。")

    assert "You are a professional coding agent" in prompt
    assert "Core Principles" in prompt
    assert "Tool Priority" in prompt
    assert "你负责做 planner。" in prompt


def test_codex_system_prompt_loads_file_reader_workflow_markdown():
    prompt = build_codex_system_prompt("你负责做 file reader planner。", workflow_name="file_reader")

    assert "file_reader workflow" in prompt
    assert "excerpt" in prompt or "片段" in prompt


def test_codex_system_prompt_loads_change_and_verify_workflow_markdown():
    prompt = build_codex_system_prompt("你负责做 change planner。", workflow_name="change_and_verify")

    assert "change_and_verify workflow" in prompt
    assert "verification" in prompt.lower() or "验证" in prompt


def test_codex_system_prompt_can_load_md_backed_planner_prompt():
    prompt = build_codex_system_prompt("planner", workflow_name="change_and_verify")

    assert "You are a professional coding agent" in prompt
    assert "change_and_verify workflow" in prompt


def test_workflow_registry_loads_markdown_definitions():
    registry = WorkflowRegistry.default()

    workflow = registry.require_for_task_profile("change_and_verify")

    assert workflow.name == "change_and_verify"
    assert "验证" in workflow.instructions or "verification" in workflow.instructions.lower()


def test_run_context_builder_collects_workspace_memory_and_plan_state(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "note.md"
    file_path.write_text("hello", encoding="utf-8")
    context = _context(workspace)
    context.application_context.config["instructions"] = ["workspace:AGENTS.md"]
    context.application_context.services["loaded_instructions"] = ["user:~/.config/agent/instructions.md"]
    context.application_context.session_memory.remember_focus([ResourceRef.for_path(file_path)], summary="note summary")
    context.application_context.index_memory.put("loaded_instructions", ["memory:MEMORY.md"])
    task = CodexTask(
        goal="读取 note.md",
        actions=[CodexAction(kind="call_tool", instruction="read note", status="completed", observation="note content")],
        task_profile="file_reader",
        runtime_persona="explore",
        plan=CodexPlan(tasks=[CodexPlanTask(title="Read note", kind="gather_context", status="in_progress")]),
    )
    task.memory.known_facts.append("note exists")
    task.memory.open_questions.append("what changed?")

    snapshot = build_run_context(context, task=task, session=context.session, user_input=task.goal)

    assert snapshot.identity["active_agent"] == "codex"
    assert snapshot.identity["persona"] == "explore"
    assert snapshot.identity["task_profile"] == "file_reader"
    assert snapshot.workspace["cwd"] == str(workspace)
    assert "workspace:AGENTS.md" in snapshot.loaded_instructions
    assert "user:~/.config/agent/instructions.md" in snapshot.loaded_instructions
    assert "memory:MEMORY.md" in snapshot.loaded_instructions
    assert any("note.md" in item for item in snapshot.focused_resources)
    assert any("read note" in item or "call_tool" in item for item in snapshot.recent_completed_actions)
    assert snapshot.current_plan_state["tasks"] == ["Read note [in_progress] kind=gather_context"]
    assert snapshot.memory_snapshot["known_facts"] == ["note exists"]
    assert snapshot.memory_snapshot["open_questions"] == ["what changed?"]


def test_available_tool_names_hide_write_tools_for_explore_persona(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    context.services["codex_runtime_persona"] = "explore"
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    names = available_tool_names(context)

    assert "read_workspace_text" in names
    assert "list_workspace_directory" in names
    assert "edit_workspace_text" not in names
    assert "delete_workspace_path" not in names


def test_plan_persona_denies_write_tool_execution(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "draft.txt"
    target.write_text("old text", encoding="utf-8")
    context = _context(workspace)
    context.services["codex_runtime_persona"] = "plan"
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["action_planner"] = lambda _user_input, _session, _ctx: [
        CodexAction(
            kind="edit_text",
            instruction="edit file",
            risk_class="high",
            metadata={
                "_approval_granted": True,
                "tool_name": "edit_workspace_text",
                "arguments": {"path": "draft.txt", "content": "new text"},
            },
        )
    ]

    result = CodexAgentLoop(context).run("编辑 draft.txt 内容 new text")

    assert result.status == "failed"
    assert "does not allow tool" in result.final_output
    assert target.read_text(encoding="utf-8") == "old text"


def test_explore_persona_asks_approval_for_shell_tool_even_when_action_is_low_risk(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    context.services["codex_runtime_persona"] = "explore"
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["action_planner"] = lambda _user_input, _session, _ctx: [
        CodexAction(
            kind="call_tool",
            instruction="pwd",
            risk_class="low",
            metadata={"tool_name": "run_shell_command", "arguments": {"command": "pwd"}},
        )
    ]

    pending = CodexAgentLoop(context).run("执行 pwd")

    assert pending.status == "needs_approval"
    assert pending.approval_request is not None
    assert "requires confirmation" in pending.approval_request.reason


def test_run_context_builder_discovers_path_local_instructions(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    note = docs / "note.md"
    note.write_text("hello", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("root rules", encoding="utf-8")
    (docs / "AGENTS.md").write_text("docs rules", encoding="utf-8")
    context = _context(workspace)
    task = CodexTask(
        goal="读取 docs/note.md",
        actions=[
            CodexAction(
                kind="call_tool",
                instruction="resolve target",
                status="completed",
                metadata={"result": {"tool_output": {"resolved_path": str(note), "path": str(note)}}},
            )
        ],
        task_profile="file_reader",
    )

    snapshot = build_run_context(context, task=task, session=context.session, user_input=task.goal)

    assert str(workspace / "AGENTS.md") in snapshot.loaded_instructions
    assert str(docs / "AGENTS.md") in snapshot.loaded_instructions


def test_reading_nested_file_tracks_loaded_instructions_for_follow_up(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    note = docs / "note.md"
    note.write_text("hello", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("root rules", encoding="utf-8")
    (docs / "AGENTS.md").write_text("docs rules", encoding="utf-8")
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("读取 docs/note.md")

    assert result.status == "completed"
    loaded = context.application_context.services.get("loaded_instructions")
    assert isinstance(loaded, list)
    assert str(workspace / "AGENTS.md") in loaded
    assert str(docs / "AGENTS.md") in loaded
    persisted = context.application_context.index_memory.get("loaded_instructions")
    assert str(docs / "AGENTS.md") in persisted


def test_summary_persona_enforces_step_budget(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    context.services["codex_runtime_persona"] = "summary"
    context.services["action_planner"] = lambda _user_input, _session, _ctx: [
        CodexAction(kind="respond", instruction=f"step {index}") for index in range(5)
    ]

    result = CodexAgentLoop(context).run("请整理一下")

    assert result.status == "failed"
    assert "step budget exceeded" in result.final_output


def test_codex_loop_repairs_tool_name_case_before_execution(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["action_planner"] = lambda _user_input, _session, _ctx: [
        CodexAction(
            kind="call_tool",
            instruction="read readme",
            metadata={"tool_name": "READ_WORKSPACE_TEXT", "arguments": {"path": "README.md"}},
        )
    ]

    result = CodexAgentLoop(context).run("读取 README.md")

    assert result.status == "completed"
    assert result.final_output == "hello"
    assert result.task.actions[0].metadata["tool_name"] == "read_workspace_text"


def test_codex_loop_returns_structured_failure_for_unknown_tool(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["action_planner"] = lambda _user_input, _session, _ctx: [
        CodexAction(
            kind="call_tool",
            instruction="read readme",
            metadata={"tool_name": "read_workspace_txt", "arguments": {"path": "README.md"}},
        )
    ]

    result = CodexAgentLoop(context).run("读取 README.md")

    assert result.status == "failed"
    assert "unknown tool" in result.final_output
    error = result.task.actions[0].metadata["result"]["error"]
    assert error["code"] == "TOOL_NOT_FOUND"
    assert "read_workspace_text" in error["available_tools"]


def test_codex_loop_retries_retriable_action_error_once(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    attempts = {"count": 0}

    def _executor(_action, _session, _ctx):
        if _action.kind == "respond":
            return CodexActionResult(status="completed", final_output=_action.instruction)
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise AppError(
                code="TEMPORARY_FAILURE",
                message="temporary failure",
                stage="execute",
                retriable=True,
            )
        return CodexActionResult(status="completed", final_output="done")

    context.services["action_executor"] = _executor
    context.services["action_planner"] = lambda _user_input, _session, _ctx: [
        CodexAction(kind="call_tool", instruction="probe"),
    ]

    result = CodexAgentLoop(context).run("probe")

    assert result.status == "completed"
    assert result.final_output == "done"
    assert attempts["count"] == 2
    assert result.task.actions[0].metadata["_retry_count"] == 1


def test_codex_loop_does_not_retry_high_risk_action_error(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    attempts = {"count": 0}

    def _executor(_action, _session, _ctx):
        attempts["count"] += 1
        raise AppError(
            code="TEMPORARY_FAILURE",
            message="temporary failure",
            stage="execute",
            retriable=True,
        )

    context.services["action_executor"] = _executor
    context.services["action_planner"] = lambda _user_input, _session, _ctx: [
        CodexAction(kind="apply_patch", instruction="modify", risk_class="high", metadata={"_approval_granted": True}),
    ]

    result = CodexAgentLoop(context).run("modify")

    assert result.status == "failed"
    assert result.final_output == "temporary failure"
    assert attempts["count"] == 1


def test_codex_loop_retries_failed_result_marked_retriable(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    attempts = {"count": 0}

    def _executor(_action, _session, _ctx):
        if _action.kind == "respond":
            return CodexActionResult(status="completed", final_output=_action.instruction)
        attempts["count"] += 1
        if attempts["count"] == 1:
            return CodexActionResult(
                status="failed",
                final_output="temporary failure",
                metadata={"error": {"retriable": True, "code": "TEMPORARY_FAILURE"}},
            )
        return CodexActionResult(status="completed", final_output="done")

    context.services["action_executor"] = _executor
    context.services["action_planner"] = lambda _user_input, _session, _ctx: [
        CodexAction(kind="call_tool", instruction="probe"),
    ]

    result = CodexAgentLoop(context).run("probe")

    assert result.status == "completed"
    assert result.final_output == "done"
    assert attempts["count"] == 2
    assert result.task.actions[0].metadata["_retry_count"] == 1


def test_codex_loop_inserts_recovery_task_after_retriable_error_exhausts_retries(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.application_context.config["codex_retry_limit"] = 0
    llm = _SequenceLLM(
        [
            (
                '{"tasks":[{"kind":"recover_failed_action","title":"Inspect directory instead",'
                '"tool_name":"inspect_workspace_path","arguments":{"path":"repo"},"risk_class":"low"}]}'
            )
        ]
    )
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    gather_task = CodexPlanTask(
        title="Gather repository context",
        kind="gather_context",
        metadata={"path": "repo"},
    )
    plan = CodexPlan(
        tasks=[
            gather_task,
            CodexPlanTask(
                title="Synthesize repository overview",
                kind="synthesize_answer",
                depends_on=[gather_task.task_id],
            ),
        ],
        metadata={"workspace_root": str(workspace)},
    )

    context.services["action_planner"] = lambda user_input, _session, _ctx: CodexTask(
        goal=user_input,
        actions=[],
        task_profile="repository_explainer",
        plan=plan,
    )

    attempts = {"gather": 0, "recover": 0}

    def _executor(action, _session, _ctx):
        if action.kind == "respond":
            return CodexActionResult(status="completed", final_output=action.instruction)
        if action.metadata.get("tool_name") == "list_workspace_directory":
            attempts["gather"] += 1
            raise AppError(
                code="TEMPORARY_FAILURE",
                message="temporary failure",
                stage="execute",
                retriable=True,
            )
        if action.metadata.get("tool_name") == "inspect_workspace_path":
            attempts["recover"] += 1
            return CodexActionResult(status="completed", final_output="repo structure")
        return CodexActionResult(status="failed", final_output="unexpected action")

    context.services["action_executor"] = _executor

    result = CodexAgentLoop(context).run("repo 这个目录下面都在讲什么")

    assert result.status == "completed"
    assert [item.kind for item in result.task.plan.tasks] == [
        "gather_context",
        "recover_failed_action",
        "synthesize_answer",
    ]
    assert [action.metadata.get("tool_name") for action in result.task.actions if action.kind == "call_tool"] == [
        "list_workspace_directory",
        "inspect_workspace_path",
    ]
    assert result.task.actions[0].status == "failed"
    assert attempts == {"gather": 1, "recover": 1}
    assert llm.completions.calls


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


def test_memory_aware_target_resolution_prefers_relevant_remembered_path(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    src = workspace / "src"
    src.mkdir()
    (docs / "service.md").write_text("# service docs\n", encoding="utf-8")
    (src / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")

    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.application_context.index_memory.remember(
        MemoryRecord(
            key="focus:src/service.py",
            text="service module business logic",
            kind="workspace_focus",
            metadata={"path": "src/service.py"},
        )
    )

    tool = context.application_context.tools.require("resolve_workspace_target")
    result = execute_tool_call(
        tool,
        ToolCall(
            tool_name="resolve_workspace_target",
            arguments={"query": "continue with the service module", "target_hint": "service"},
        ),
        task=SimpleNamespace(kind="call_tool", metadata={}),
        context=context,
    )

    assert result.success is True
    assert result.output["best_match"] == "src/service.py"


def test_memory_aware_target_resolution_uses_task_conclusion_records(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    src = workspace / "src"
    src.mkdir()
    (docs / "auth.md").write_text("# auth docs\n", encoding="utf-8")
    (src / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")

    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.application_context.index_memory.remember(
        MemoryRecord(
            key="task:auth-module",
            text="auth package module login business logic conclusion",
            kind="task_conclusion",
            metadata={"path": "src/auth.py"},
        )
    )

    tool = context.application_context.tools.require("resolve_workspace_target")
    result = execute_tool_call(
        tool,
        ToolCall(
            tool_name="resolve_workspace_target",
            arguments={"query": "continue with the auth business logic", "target_hint": ""},
        ),
        task=SimpleNamespace(kind="call_tool", metadata={}),
        context=context,
    )

    assert result.success is True
    assert result.output["best_match"] == "src/auth.py"


def test_follow_up_target_resolution_prefers_session_focus_for_pronouns(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")

    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.application_context.session_memory.remember_focus(
        [ResourceRef.for_path(package)],
        summary="pkg package overview",
    )

    tool = context.application_context.tools.require("resolve_workspace_target")
    result = execute_tool_call(
        tool,
        ToolCall(
            tool_name="resolve_workspace_target",
            arguments={"query": "继续说刚才那个模块", "target_hint": ""},
        ),
        task=SimpleNamespace(kind="call_tool", metadata={}),
        context=context,
    )

    assert result.success is True
    assert result.output["best_match"] == "pkg"


def test_codex_loop_falls_back_to_single_respond_action(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _attach_test_next_action_planner(_context(workspace))

    result = CodexAgentLoop(context).run("hello")

    assert result.status == "completed"
    assert "你好" in result.final_output
    assert result.task.actions[0].kind == "respond"
    assert result.task.task_profile == "chat"


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
    assert [action.kind for action in result.task.actions] == ["call_tool", "call_tool", "respond"]
    assert result.task.actions[0].metadata["tool_name"] == "resolve_workspace_target"
    assert result.task.actions[1].metadata["tool_name"] == "read_workspace_text"


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
    assert [action.metadata.get("tool_name") for action in result.task.actions if action.kind == "call_tool"][:3] == [
        "list_workspace_directory",
        "inspect_workspace_path",
        "rank_workspace_entries",
    ]
    assert any(action.metadata.get("tool_name") == "extract_workspace_outline" for action in result.task.actions if action.kind == "call_tool")
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


def test_codex_loop_replaces_workspace_text_via_default_tooling(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.md"
    target.write_text("hello old world", encoding="utf-8")
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    loop = CodexAgentLoop(context)

    pending = loop.run('替换 note.md 里的 "old" 为 "new"')

    assert pending.status == "needs_approval"
    assert pending.action_kind in {"apply_patch", "edit_text"}
    assert pending.resume_token is not None

    result = loop.resume(pending.resume_token, approved=True)

    assert result.status == "completed"
    assert target.read_text(encoding="utf-8") == "hello new world"


def test_codex_loop_appends_workspace_text_via_default_tooling(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.md"
    target.write_text("hello", encoding="utf-8")
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    loop = CodexAgentLoop(context)

    pending = loop.run('在 note.md 末尾追加 "\\nworld"')

    assert pending.status == "needs_approval"
    assert pending.action_kind in {"edit_text", "apply_patch"}
    assert pending.resume_token is not None

    result = loop.resume(pending.resume_token, approved=True)

    assert result.status == "completed"
    assert target.read_text(encoding="utf-8") == "hello\nworld"


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


def test_codex_llm_system_prompts_share_runtime_header(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("alpha\nbeta\ngamma\ndelta", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output
    context.services["model_first_task_profile_classifier"] = True
    llm = _SequenceLLM(
        [
            '{"profile":"file_reader"}',
            '{"kind":"call_tool","tool_name":"read_workspace_text","arguments":{"path":"note.md"}}',
            '{"decision":"finish"}',
        ]
    )
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    result = CodexAgentLoop(context).run("总结 note.md 主要内容")

    assert result.status == "completed"
    classifier_prompt = llm.completions.calls[0]["messages"][0]["content"]
    planner_prompt = llm.completions.calls[1]["messages"][0]["content"]
    evaluator_prompt = llm.completions.calls[2]["messages"][0]["content"]
    for prompt in (classifier_prompt, planner_prompt, evaluator_prompt):
        assert "You are a professional coding agent" in prompt
        assert "Core Principles" in prompt
        assert "Tool Priority" in prompt


def test_codex_llm_planner_injects_resource_semantics_and_follow_up_context(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.application_context.session_memory.remember_focus(
        [ResourceRef.for_path(docs / "guide.md")],
        summary="recently explained guide.md",
    )
    context.session.add_turn("assistant", "刚刚已经介绍过 docs/guide.md 的背景。")
    llm = _SequenceLLM(['{"kind":"respond","instruction":"ok"}'])
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    task = CodexTask(
        goal="继续总结刚才那个文件",
        actions=[
            CodexAction(
                kind="call_tool",
                instruction="resolve target",
                status="completed",
                metadata={
                    "tool_name": "resolve_workspace_target",
                    "arguments": {"query": "继续总结刚才那个文件", "target_hint": ""},
                    "result": {
                        "tool_output": {
                            "path": str(docs / "guide.md"),
                            "resolved_path": str(docs / "guide.md"),
                            "resource_kind": "file",
                            "is_container": False,
                            "allowed_actions": ["read", "summarize", "inspect"],
                        }
                    },
                },
                observation="Resolved target: docs/guide.md",
            )
        ],
        task_profile="file_reader",
    )

    action = context.services.get("next_action_planner")
    if not callable(action):
        planned = None
    else:
        planned = action(task, context.session, context, list(context.application_context.tools.names()))
    if planned is None:
        from agent_runtime_framework.agents.codex.planner import plan_next_codex_action

        planned = plan_next_codex_action(task, context.session, context)

    assert planned is not None
    planner_prompt = llm.completions.calls[0]["messages"][-1]["content"]
    assert "Resource semantics:" in planner_prompt
    assert "resource_kind: file" in planner_prompt
    assert "allowed_actions: read, summarize, inspect" in planner_prompt
    assert "Recent focused resources:" in planner_prompt
    assert "guide.md" in planner_prompt
    assert "Recent turns:" in planner_prompt


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


def test_codex_excerpt_tool_returns_agent_friendly_excerpt(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("alpha\nbeta\ngamma\ndelta", encoding="utf-8")
    context = _context(workspace)
    tool = next(tool for tool in build_default_codex_tools() if tool.name == "read_workspace_excerpt")

    output = tool.executor(None, context, {"path": "note.md", "max_lines": 2})

    assert output["text"].splitlines()[:2] == ["alpha", "beta"]
    assert output["summary"]
    assert output["next_hint"]


def test_codex_rank_workspace_entries_prefers_representative_files(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text('"""Package entry."""\n', encoding="utf-8")
    (package / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    (package / "helpers.py").write_text("def helper():\n    return 'x'\n", encoding="utf-8")
    context = _context(workspace)
    tool = next(tool for tool in build_default_codex_tools() if tool.name == "rank_workspace_entries")

    output = tool.executor(None, context, {"path": "pkg", "query": "请讲解 pkg 这个 package 的结构和主要职责"})

    assert output["ranked_paths"][0] == "pkg/__init__.py"
    assert "pkg/service.py" in output["ranked_paths"]


def test_codex_extract_workspace_outline_summarizes_python_symbols(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "service.py").write_text(
        '"""Service module."""\n\nclass Service:\n    pass\n\ndef run():\n    return "ok"\n',
        encoding="utf-8",
    )
    context = _context(workspace)
    tool = next(tool for tool in build_default_codex_tools() if tool.name == "extract_workspace_outline")

    output = tool.executor(None, context, {"path": "pkg/service.py"})

    assert "pkg/service.py" in output["text"]
    assert "Service module." in output["text"] or "定义了 Service, run" in output["text"]


def test_codex_inspect_tool_handles_non_utf8_files_gracefully(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    (package / "cache.pyc").write_bytes(b"\x80\x04\x95binary-data")
    context = _context(workspace)
    tool = next(tool for tool in build_default_codex_tools() if tool.name == "inspect_workspace_path")

    output = tool.executor(None, context, {"path": "pkg"})

    assert "service.py" in output["text"]
    assert "cache.pyc" in output["text"]
    assert "binary" in output["text"] or "non-utf-8" in output["text"].lower() or "二进制" in output["text"] or "非文本" in output["text"] or "无法按 UTF-8 读取" in output["text"]


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
    assert "输出已截断" in (result.task.actions[0].observation or "") or "truncated" in (result.task.actions[0].observation or "").lower()


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
    assert result.task.task_profile == "repository_explainer"
    assert len(result.task.actions) >= 3
    subgoals = [action.subgoal for action in result.task.actions]
    assert "gather_evidence" in subgoals
    assert "synthesize_answer" in subgoals
    assert "service.py" in result.final_output


def test_codex_task_profile_routes_change_and_verify_requests(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("编辑 draft.txt 内容 new text 并运行验证 pytest -q")

    assert result.task.task_profile == "change_and_verify"


def test_codex_task_profile_routes_file_reader_requests(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
    context = _attach_test_next_action_planner(_context(workspace))
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("总结 README.md 主要内容")

    assert result.task.task_profile == "file_reader"


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
    assert "service.py" in result.final_output
    assert "run" in result.final_output


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
    assert "service.py" in result.final_output
    assert "utils.py" in result.final_output


def test_repository_explainer_profile_prefers_repository_style_summary(tmp_path: Path):
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

    result = CodexAgentLoop(context).run("请讲解 pkg 这个 package 的结构")

    assert result.task.task_profile == "repository_explainer"
    assert "目录结构" in result.final_output
    assert "关键文件" in result.final_output or "作用" in result.final_output


def test_evidence_sufficiency_uses_resource_semantics_instead_of_tool_name(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    task = CodexTask(
        goal="请讲解 pkg 这个 package 的结构",
        actions=[
            CodexAction(
                kind="call_tool",
                instruction="custom probe",
                status="completed",
                observation="下面一共有 1 个条目。\n文件：__init__.py",
                metadata={
                    "tool_name": "custom_workspace_probe",
                    "arguments": {"path": "pkg"},
                    "result": {
                        "tool_output": {
                            "path": str(package),
                            "resolved_path": str(package),
                            "resource_kind": "directory",
                            "is_container": True,
                            "allowed_actions": ["list", "inspect"],
                        }
                    },
                },
            )
        ],
        task_profile="repository_explainer",
    )

    decision = evaluate_codex_output(task, None, context, list(context.application_context.tools.names()))

    assert decision.status == "continue"
    assert decision.next_action is not None
    assert decision.next_action.metadata["tool_name"] == "inspect_workspace_path"
    assert decision.next_action.metadata["arguments"]["path"] == "pkg"


def test_file_reader_evidence_sufficiency_requests_file_content_when_only_target_is_resolved(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readme = workspace / "README.md"
    readme.write_text("# Demo\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    task = CodexTask(
        goal="总结 README.md 主要内容",
        actions=[
            CodexAction(
                kind="call_tool",
                instruction="resolve target",
                status="completed",
                observation="Resolved target: README.md",
                metadata={
                    "tool_name": "resolve_workspace_target",
                    "arguments": {"query": "总结 README.md 主要内容", "target_hint": "README.md"},
                    "result": {
                        "tool_output": {
                            "path": str(readme),
                            "resolved_path": str(readme),
                            "resource_kind": "file",
                            "is_container": False,
                            "allowed_actions": ["read", "summarize", "inspect"],
                        }
                    },
                },
            )
        ],
        task_profile="file_reader",
    )

    decision = evaluate_codex_output(task, None, context, list(context.application_context.tools.names()))

    assert decision.status == "continue"
    assert decision.next_action is not None
    assert decision.next_action.metadata["tool_name"] in {"summarize_workspace_text", "read_workspace_text"}
    assert decision.next_action.metadata["arguments"]["path"] == "README.md"


def test_repository_explainer_profile_has_default_strategy_without_custom_planner(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "agent_runtime_framework"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "assistant.py").write_text("def run():\n    pass\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output

    result = CodexAgentLoop(context).run("agent_runtime_framework 下面主要都有哪些文件都是在做什么")

    assert result.task.task_profile == "repository_explainer"
    assert len(result.task.actions) >= 2
    assert result.task.actions[0].kind == "call_tool"
    assert result.task.actions[0].metadata["tool_name"] == "resolve_workspace_target"
    assert result.task.actions[1].kind == "call_tool"
    assert result.task.actions[1].metadata["tool_name"] == "list_workspace_directory"
    assert "agent_runtime_framework" in result.final_output


def test_codex_loop_persists_task_conclusion_into_index_memory(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text('"""Package entry."""\n', encoding="utf-8")
    (package / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("请讲解 pkg 这个 package 的结构")
    matches = context.application_context.index_memory.search("pkg package structure", kind="task_conclusion")

    assert result.status == "completed"
    assert matches
    assert matches[0].metadata["path"] == "pkg"
    assert "pkg" in matches[0].text


def test_repository_explainer_profile_handles_natural_directory_question(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "agent_runtime_framework"
    package.mkdir()
    (package / "__init__.py").write_text('"""Runtime package entry."""\n', encoding="utf-8")
    (package / "assistant.py").write_text("def run():\n    pass\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output

    result = CodexAgentLoop(context).run("agent_runtime_framework这个目录下面都是在讲什么呢？")

    assert result.status == "completed"
    assert result.task.task_profile == "repository_explainer"
    assert result.task.actions[0].metadata["tool_name"] == "resolve_workspace_target"
    assert result.task.actions[1].metadata["tool_name"] == "list_workspace_directory"
    assert any(item.kind == "inspect_target" for item in (result.task.plan.tasks if result.task.plan else []))
    assert "assistant.py" in result.final_output


def test_repository_explainer_asks_for_clarification_when_target_is_ambiguous(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = workspace / "src"
    src.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    (src / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    (docs / "service.md").write_text("# service docs\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("请讲解 service 这个模块在做什么")

    assert result.status == "needs_clarification"
    assert result.task.actions[0].metadata["tool_name"] == "resolve_workspace_target"
    assert result.task.actions[-1].kind == "respond"
    assert "多个可能目标" in result.final_output
    assert "src/service.py" in result.final_output
    assert "docs/service.md" in result.final_output


def test_repository_explainer_can_resume_after_target_clarification(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = workspace / "src"
    src.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    (src / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    (docs / "service.md").write_text("# service docs\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output

    loop = CodexAgentLoop(context)
    first = loop.run("请讲解 service 这个模块在做什么")
    second = loop.run("src/service.py")

    assert first.status == "needs_clarification"
    assert second.status == "completed"
    assert second.task.task_profile == "repository_explainer"
    assert "src/service.py" in second.final_output
    assert "多个可能目标" not in second.final_output


def test_repository_explainer_can_resume_after_restart_with_persisted_clarification(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src = workspace / "src"
    src.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    (src / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    (docs / "service.md").write_text("# service docs\n", encoding="utf-8")

    first_context = CodexContext(
        application_context=ApplicationContext(
            resource_repository=LocalFileResourceRepository([workspace]),
            session_memory=InMemorySessionMemory(),
            policy=SimpleDesktopPolicy(),
            tools=ToolRegistry(),
            config={"default_directory": str(workspace)},
        ),
        session=AssistantSession(session_id="codex-first"),
    )
    for tool in build_default_codex_tools():
        first_context.application_context.tools.register(tool)
    first_context.services["output_evaluator"] = evaluate_codex_output

    first = CodexAgentLoop(first_context).run("请讲解 service 这个模块在做什么")

    restarted_context = CodexContext(
        application_context=ApplicationContext(
            resource_repository=LocalFileResourceRepository([workspace]),
            session_memory=InMemorySessionMemory(),
            policy=SimpleDesktopPolicy(),
            tools=ToolRegistry(),
            config={"default_directory": str(workspace)},
        ),
        session=AssistantSession(session_id="codex-restarted"),
    )
    for tool in build_default_codex_tools():
        restarted_context.application_context.tools.register(tool)
    restarted_context.services["output_evaluator"] = evaluate_codex_output

    second = CodexAgentLoop(restarted_context).run("src/service.py")

    assert first.status == "needs_clarification"
    assert second.status == "completed"
    assert "src/service.py" in second.final_output


def test_repository_explainer_profile_creates_task_level_plan(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "agent_runtime_framework"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "assistant.py").write_text("def run():\n    pass\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output

    result = CodexAgentLoop(context).run("agent_runtime_framework 下面主要都有哪些文件都是在做什么")

    assert result.task.plan is not None
    assert [item.kind for item in result.task.plan.tasks[:4]] == [
        "locate_target",
        "gather_context",
        "inspect_target",
        "rank_representative_files",
    ]
    assert result.task.plan.tasks[-1].kind == "synthesize_answer"
    assert any(item.kind == "extract_outline" for item in result.task.plan.tasks)
    assert all(item.status == "completed" for item in result.task.plan.tasks)
    assert result.task.plan.tasks[0].action_indexes == [0]
    assert result.task.plan.tasks[1].action_indexes == [1]
    assert result.task.plan.tasks[2].action_indexes == [2]
    assert result.task.plan.tasks[3].action_indexes == [3]
    assert all(item.action_indexes for item in result.task.plan.tasks[4:])


def test_repository_explainer_plan_uses_shared_task_kinds(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output

    result = CodexAgentLoop(context).run("请讲解 pkg 这个 package 的结构")

    assert result.task.plan is not None
    assert [item.kind for item in result.task.plan.tasks[:4]] == [
        "locate_target",
        "gather_context",
        "inspect_target",
        "rank_representative_files",
    ]
    assert result.task.plan.tasks[-1].kind == "synthesize_answer"
    assert any(item.kind == "extract_outline" for item in result.task.plan.tasks)


def test_repository_explainer_plan_inserts_inspect_task_after_locate(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output

    task = CodexTask(goal="请讲解 pkg 这个 package 的结构", actions=[], task_profile="repository_explainer")
    task.plan = build_task_plan(task, context)

    assert task.plan is not None
    assert [item.kind for item in task.plan.tasks] == [
        "locate_target",
        "gather_context",
        "synthesize_answer",
    ]

    result = CodexAgentLoop(context).run(task.goal)

    assert [item.kind for item in result.task.plan.tasks[:4]] == [
        "locate_target",
        "gather_context",
        "inspect_target",
        "rank_representative_files",
    ]
    assert result.task.plan.tasks[-1].kind == "synthesize_answer"
    assert any(item.kind == "extract_outline" for item in result.task.plan.tasks)


def test_repository_overview_workflow_reads_representative_outline_before_summary(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "agent_runtime_framework"
    package.mkdir()
    (package / "__init__.py").write_text('"""Runtime package entry."""\n', encoding="utf-8")
    (package / "assistant.py").write_text("class Assistant:\n    pass\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output

    result = CodexAgentLoop(context).run("agent_runtime_framework这个目录下面主要都有什么呢？给我简单讲解一下")

    assert result.status == "completed"
    tool_names = [action.metadata.get("tool_name") for action in result.task.actions if action.kind == "call_tool"]
    assert tool_names[:4] == [
        "resolve_workspace_target",
        "list_workspace_directory",
        "inspect_workspace_path",
        "rank_workspace_entries",
    ]
    assert tool_names.count("extract_workspace_outline") >= 1
    assert "assistant.py" in result.final_output
    assert "Runtime package entry." in result.final_output or "Assistant" in result.final_output


def test_file_reader_profile_creates_task_level_plan(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("总结 README.md 主要内容")

    assert result.status == "completed"
    assert result.task.plan is not None
    assert result.task.task_profile == "file_reader"
    assert [item.kind for item in result.task.plan.tasks] == [
        "locate_target",
        "gather_context",
        "synthesize_answer",
    ]
    assert result.task.actions[0].metadata["tool_name"] == "resolve_workspace_target"
    assert result.task.actions[1].metadata["tool_name"] in {"read_workspace_excerpt", "read_workspace_text"}


def test_file_reader_summary_requests_prefer_excerpt_primitive(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\nalpha\nbeta\ngamma\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("总结 README.md 主要内容")

    assert result.status == "completed"
    assert result.task.task_profile == "file_reader"
    assert result.task.actions[1].metadata["tool_name"] == "read_workspace_excerpt"


def test_resource_semantics_make_repository_explainer_read_file_targets(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readme = workspace / "README.md"
    readme.write_text("# Demo\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    task = CodexTask(goal="解释 README.md 在讲什么", actions=[], task_profile="repository_explainer")
    task.plan = build_task_plan(task, context)
    assert task.plan is not None

    locate_task_id = task.plan.tasks[0].task_id
    locate_action = CodexAction(
        kind="call_tool",
        instruction=task.goal,
        status="completed",
        metadata={
            "tool_name": "resolve_workspace_target",
            "plan_task_id": locate_task_id,
        },
    )
    task.actions.append(locate_action)
    attach_action_to_plan(task, locate_action, 0)

    locate_result = CodexActionResult(
        status="completed",
        final_output="Resolved target: README.md",
        metadata={
            "tool_output": {
                "path": str(readme),
                "resolved_path": str(readme),
                "resource_kind": "file",
                "is_container": False,
                "allowed_actions": ["read", "summarize", "inspect"],
            }
        },
    )

    advance_task_plan(task, locate_action, locate_result, context)
    next_action = plan_next_task_action(task)

    assert task.plan.target_semantics is not None
    assert task.plan.target_semantics.resource_kind == "file"
    assert [item.kind for item in task.plan.tasks] == [
        "locate_target",
        "gather_context",
        "synthesize_answer",
    ]
    assert next_action is not None
    assert next_action.metadata["tool_name"] == "read_workspace_text"
    assert next_action.metadata["arguments"]["path"] == str(readme)


def test_repository_explainer_uses_llm_to_insert_read_entrypoint(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text('"""Package entry."""\n', encoding="utf-8")
    (package / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(
        [
            '{"best_match":"pkg","candidates":["pkg"]}',
            '{"tasks":[{"kind":"read_entrypoint","path":"pkg/__init__.py","title":"Read package entrypoint"}]}',
        ]
    )
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    result = CodexAgentLoop(context).run("请讲解 pkg 这个 package 的结构")

    assert result.status == "completed"
    assert [item.kind for item in result.task.plan.tasks[:4]] == [
        "locate_target",
        "gather_context",
        "inspect_target",
        "rank_representative_files",
    ]
    assert result.task.plan.tasks[-1].kind == "synthesize_answer"
    assert any(item.kind == "extract_outline" for item in result.task.plan.tasks)
    assert [action.kind for action in result.task.actions[:5]] == [
        "call_tool",
        "call_tool",
        "call_tool",
        "call_tool",
        "call_tool",
    ]
    assert result.task.actions[0].metadata["tool_name"] == "resolve_workspace_target"
    assert result.task.actions[3].metadata["tool_name"] == "rank_workspace_entries"
    assert any(action.metadata.get("tool_name") == "extract_workspace_outline" for action in result.task.actions if action.kind == "call_tool")
    assert llm.completions.calls


def test_repository_explainer_entrypoint_read_improves_summary(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text('"""Package entry."""\n', encoding="utf-8")
    (package / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.application_context.llm_client = _SequenceLLM(
        ['{"tasks":[{"kind":"read_entrypoint","path":"pkg/__init__.py","title":"Read package entrypoint"}]}']
    )
    context.application_context.llm_model = "test-model"

    result = CodexAgentLoop(context).run("请讲解 pkg 这个 package 的结构")

    assert result.status == "completed"
    assert "Package entry." in result.final_output
    assert "引用：" in result.final_output


def test_change_and_verify_uses_llm_to_insert_repair_after_failed_verification(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "draft.txt"
    target.write_text("old text", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(
        [
            '{"tasks":[{"kind":"repair_after_failed_verification","title":"Repair failing draft","tool_name":"edit_workspace_text","path":"draft.txt","content":"fixed text"}]}'
        ]
    )
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"
    verification_attempts = {"count": 0}

    def _executor(action, _session, _ctx):
        if action.kind == "call_tool" and action.metadata.get("tool_name") == "resolve_workspace_target":
            return {
                "status": "completed",
                "final_output": "Resolved target: draft.txt",
                "metadata": {
                    "tool_output": {
                        "path": str(target),
                        "resolved_path": str(target),
                        "resource_kind": "file",
                        "is_container": False,
                        "allowed_actions": ["read", "summarize", "inspect"],
                    }
                },
            }
        if action.kind == "run_verification":
            verification_attempts["count"] += 1
            if verification_attempts["count"] == 1:
                return {
                    "status": "failed",
                    "final_output": "verification failed",
                    "metadata": {"verification": {"success": False, "summary": "verification failed", "command": "false"}},
                }
            return {
                "status": "completed",
                "final_output": "verification passed",
                "metadata": {"verification": {"success": True, "summary": "verification passed", "command": "false"}},
            }
        if action.kind == "edit_text":
            target.write_text("fixed text", encoding="utf-8")
            return {
                "status": "completed",
                "final_output": "fixed text",
                "metadata": {"tool_output": {"path": "draft.txt", "changed_paths": ["draft.txt"], "text": "fixed text"}},
            }
        if action.kind == "respond":
            return {"status": "completed", "final_output": action.instruction}
        raise AssertionError(f"unexpected action: {action.kind}")

    context.services["action_executor"] = _executor

    loop = CodexAgentLoop(context)
    pending = loop.run("编辑 draft.txt 内容 broken text 并运行验证 false")
    result = loop.resume(pending.resume_token, approved=True)
    if result.status == "needs_approval":
        result = loop.resume(result.resume_token, approved=True)
    if result.status == "needs_approval":
        result = loop.resume(result.resume_token, approved=True)

    assert result.status == "completed"
    assert [item.kind for item in result.task.plan.tasks] == [
        "locate_target",
        "modify_target",
        "run_verification",
        "repair_after_failed_verification",
        "run_verification",
        "synthesize_answer",
    ]
    assert target.read_text(encoding="utf-8") == "fixed text"
    assert llm.completions.calls
    assert verification_attempts["count"] == 2
    assert "verification passed" in result.final_output


def test_change_and_verify_re_runs_verification_after_repair(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "draft.txt"
    target.write_text("old text", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(
        [
            '{"tasks":[{"kind":"repair_after_failed_verification","title":"Repair failing draft","tool_name":"edit_workspace_text","path":"draft.txt","content":"fixed text"}]}'
        ]
    )
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    verification_attempts = {"count": 0}
    original_executor = context.services.get("action_executor")

    def _executor(action, session, ctx):
        if action.kind == "call_tool" and action.metadata.get("tool_name") == "resolve_workspace_target":
            return {
                "status": "completed",
                "final_output": "Resolved target: draft.txt",
                "metadata": {
                    "tool_output": {
                        "path": str(target),
                        "resolved_path": str(target),
                        "resource_kind": "file",
                        "is_container": False,
                        "allowed_actions": ["read", "summarize", "inspect"],
                    }
                },
            }
        if action.kind == "run_verification":
            verification_attempts["count"] += 1
            if verification_attempts["count"] == 1:
                return {
                    "status": "failed",
                    "final_output": "verification failed",
                    "metadata": {"verification": {"success": False, "summary": "verification failed", "command": "false"}},
                }
            return {
                "status": "completed",
                "final_output": "verification passed",
                "metadata": {"verification": {"success": True, "summary": "verification passed", "command": "false"}},
            }
        if action.kind == "edit_text":
            return {
                "status": "completed",
                "final_output": "fixed text",
                "metadata": {"tool_output": {"path": "draft.txt", "changed_paths": ["draft.txt"], "text": "fixed text"}},
            }
        if action.kind == "respond":
            return {"status": "completed", "final_output": action.instruction}
        if callable(original_executor):
            return original_executor(action, session, ctx)
        raise AssertionError(f"unexpected action: {action.kind}")

    context.services["action_executor"] = _executor

    loop = CodexAgentLoop(context)
    pending = loop.run("编辑 draft.txt 内容 broken text 并运行验证 false")
    result = loop.resume(pending.resume_token, approved=True)
    if result.status == "needs_approval":
        result = loop.resume(result.resume_token, approved=True)
    if result.status == "needs_approval":
        result = loop.resume(result.resume_token, approved=True)

    assert result.status == "completed"
    assert verification_attempts["count"] == 2
    assert "verification passed" in result.final_output
    assert [item.kind for item in result.task.plan.tasks] == [
        "locate_target",
        "modify_target",
        "run_verification",
        "repair_after_failed_verification",
        "run_verification",
        "synthesize_answer",
    ]


def test_repository_explainer_summary_lists_reference_paths(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text('"""Package entry."""\n', encoding="utf-8")
    (package / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.application_context.llm_client = _SequenceLLM(
        [
            '{"best_match":"pkg","candidates":["pkg"]}',
            '{"tasks":[{"kind":"read_entrypoint","path":"pkg/__init__.py","title":"Read package entrypoint"}]}',
        ]
    )
    context.application_context.llm_model = "test-model"

    result = CodexAgentLoop(context).run("请讲解 pkg 这个 package 的结构")

    assert result.status == "completed"
    assert "pkg/__init__.py" in result.final_output


def test_benchmark_memory_folder_question_scores_high(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "agent_runtime_framework"
    memory_dir = package / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "__init__.py").write_text("", encoding="utf-8")
    (memory_dir / "index.py").write_text("class IndexMemory:\n    pass\n", encoding="utf-8")
    (memory_dir / "session.py").write_text("class SessionMemory:\n    pass\n", encoding="utf-8")
    (memory_dir / "working.py").write_text("class WorkingMemory:\n    pass\n", encoding="utf-8")
    context, llm = _benchmark_context(
        workspace,
        llm_contents=[
            '{"profile":"repository_explainer"}',
            '{"best_match":"agent_runtime_framework/memory","candidates":["agent_runtime_framework/memory"]}',
        ],
    )

    result = CodexAgentLoop(context).run("memory文件夹下面都有什么内容呢？")

    assert llm is not None
    assert _score_workspace_question(
        result,
        expected_profile="repository_explainer",
        expected_refs=["agent_runtime_framework/memory"],
        expected_tokens=["index.py", "session.py", "working.py"],
    ) >= 4


def test_benchmark_current_directory_question_scores_high(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
    (workspace / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (workspace / "tests").mkdir()
    context, llm = _benchmark_context(
        workspace,
        llm_contents=[
            '{"profile":"repository_explainer"}',
            '{"best_match":".","candidates":["."]}',
        ],
    )

    result = CodexAgentLoop(context).run("我当前目录下都有哪些文件呢？")

    assert llm is not None
    assert _score_workspace_question(
        result,
        expected_profile="repository_explainer",
        expected_refs=["."],
        expected_tokens=["README.md", "pyproject.toml", "tests"],
    ) >= 4


def test_current_working_directory_phrase_routes_to_workspace_root(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
    (workspace / "src").mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("给我列一下我当前的工作目录都有哪些文件呢")

    assert result.status == "completed"
    assert result.task.task_profile == "repository_explainer"
    assert "README.md" in result.final_output
    assert "目录结构" in result.final_output or "entries" in result.final_output


@pytest.mark.xfail(reason="当前对无明确 target 的根目录列举仍偏保守，容易要求澄清而不是默认落到 workspace 根目录。", strict=False)
def test_intelligence_assessment_directory_listing_without_explicit_target_prefers_workspace_root(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
    (workspace / "docs").mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("帮我列一下文件目录")

    assert result.status == "completed"
    assert result.task.task_profile == "repository_explainer"
    assert _tool_trace(result)[:2] == ["resolve_workspace_target", "list_workspace_directory"]
    assert "README.md" in result.final_output
    assert "docs" in result.final_output


@pytest.mark.xfail(reason="当前对“列目录并总结/项目摘要”这类根目录概览请求仍偏保守，缺少默认 workspace 级语义。", strict=False)
def test_intelligence_assessment_directory_listing_and_summary_uses_structure_then_representative_files(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\nProject overview.\n", encoding="utf-8")
    pkg = workspace / "src"
    pkg.mkdir()
    (pkg / "service.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output

    result = CodexAgentLoop(context).run("帮我列一下文件目录并帮我总结")

    assert result.status == "completed"
    assert result.task.task_profile == "repository_explainer"
    assert "inspect_workspace_path" in _tool_trace(result)
    assert any(tool in _tool_trace(result) for tool in {"rank_workspace_entries", "read_workspace_text", "extract_workspace_outline"})
    assert "README.md" in result.final_output
    assert "service.py" in result.final_output


@pytest.mark.xfail(reason="当前“项目摘要”仍没有稳定映射为当前 workspace 的 repo overview 任务。", strict=False)
def test_intelligence_assessment_project_summary_defaults_to_workspace_overview(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo\nA smart agent runtime framework.\n", encoding="utf-8")
    (workspace / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    pkg = workspace / "agent_runtime_framework"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    context.services["output_evaluator"] = evaluate_codex_output

    result = CodexAgentLoop(context).run("帮我生成一下该项目的摘要")

    assert result.status == "completed"
    assert result.task.task_profile == "repository_explainer"
    assert _tool_trace(result)[0] == "resolve_workspace_target"
    assert "README.md" in result.final_output
    assert "pyproject.toml" in result.final_output or "agent_runtime_framework" in result.final_output


@pytest.mark.xfail(reason="读取并讲解具体文件时，当前 profile 仍可能落到 repository_explainer，而不是更稳定的 file_reader。", strict=False)
def test_intelligence_assessment_file_read_and_explanation_prefers_file_reader(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\nThis project orchestrates agent workflows.\n", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("帮我读取 docs/guide.md 并讲一下在讲什么")

    assert result.status == "completed"
    assert result.task.task_profile == "file_reader"
    assert _tool_trace(result)[:2] == ["resolve_workspace_target", "read_workspace_text"]
    assert "agent workflows" in result.final_output


def test_change_and_verify_profile_creates_task_level_plan(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "draft.txt"
    target.write_text("old text", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    loop = CodexAgentLoop(context)
    pending = loop.run("编辑 draft.txt 内容 new text 并运行验证 pwd")

    assert pending.status == "needs_approval"
    assert pending.task.task_profile == "change_and_verify"
    assert pending.task.plan is not None
    assert [item.kind for item in pending.task.plan.tasks] == [
        "locate_target",
        "modify_target",
        "run_verification",
        "synthesize_answer",
    ]
    assert pending.task.plan.tasks[0].status == "completed"
    assert pending.task.plan.tasks[1].status == "in_progress"

    result = loop.resume(pending.resume_token, approved=True)

    assert result.status == "completed"
    assert all(item.status == "completed" for item in result.task.plan.tasks)
    assert result.task.plan.tasks[0].action_indexes == [0]
    assert result.task.plan.tasks[1].action_indexes == [1]
    assert result.task.plan.tasks[2].action_indexes == [2]
    assert result.task.plan.tasks[3].action_indexes == [3]


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


def test_change_task_completes_with_final_respond_summary(tmp_path: Path):
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
    result = loop.resume(pending.resume_token, approved=True)

    completed_actions = [action for action in result.task.actions if action.status == "completed"]
    assert result.status == "completed"
    assert completed_actions[-1].kind == "respond"
    assert completed_actions[-1].subgoal == "synthesize_answer"
    assert completed_actions[-1].metadata.get("direct_output") is True
    assert "Completed the requested update" in result.final_output
    assert "draft.txt" in result.final_output
    assert "Verification:" in result.final_output


def test_evaluator_requests_final_summary_for_change_task_without_respond(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = CodexTask(
        goal="编辑 draft.txt 内容 new text",
        actions=[
            CodexAction(
                kind="edit_text",
                instruction="edit draft.txt",
                subgoal="modify_workspace",
                status="completed",
                observation="updated draft.txt to new text",
                metadata={"tool_name": "edit_workspace_text", "arguments": {"path": "draft.txt"}},
            )
        ],
        task_profile="change_and_verify",
    )
    task.memory.modified_paths.append("draft.txt")
    context = _context(workspace)

    decision = evaluate_codex_output(task, context.session, context, ["run_tests", "get_git_diff"])

    assert decision.status == "continue"
    assert decision.next_action is not None
    assert decision.next_action.kind == "respond"
    assert decision.next_action.metadata.get("evaluator_reason") == "missing_final_summary"
    assert "Completed the requested update" in decision.next_action.instruction
    assert "draft.txt" in decision.next_action.instruction
    assert "Verification:" in decision.next_action.instruction


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
        CodexAgentLoop(context).run("please inspect the note for me")

    assert exc_info.value.code == "PLANNER_INVALID_JSON"


def test_codex_loop_accepts_minimax_tool_call_planner_format(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("hello", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(
        [
            '我来帮你读取文件。\n<minimax:tool_call>\n.kind: call_tool\n.tool_name: read_workspace_text\n.arguments: {\n  "path": "note.md"\n}\n.risk_class: low\n</invoke>'
        ]
    )
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    result = CodexAgentLoop(context).run("please inspect the note for me")

    assert result.status == "completed"
    assert result.final_output == "hello"
    assert result.task.actions[0].metadata["tool_name"] == "read_workspace_text"


def test_codex_loop_retries_planner_once_after_invalid_json(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("hello", encoding="utf-8")
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(
        [
            "not json",
            '{"kind":"call_tool","tool_name":"read_workspace_text","arguments":{"path":"note.md"}}',
        ]
    )
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    result = CodexAgentLoop(context).run("please inspect the note for me")

    assert result.status == "completed"
    assert result.final_output == "hello"
    assert len(llm.completions.calls) == 2


def test_codex_loop_requests_clarification_for_ambiguous_create_file_goal(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)

    result = CodexAgentLoop(context).run("可以为我创建一个文件吗？")

    assert result.status == "needs_clarification"
    assert result.task.task_profile == "change_and_verify"
    assert "文件名" in result.final_output or "路径" in result.final_output
    assert result.task.actions
    assert result.task.actions[-1].kind == "respond"
    assert result.task.actions[-1].metadata.get("clarification_required") is True


def test_codex_loop_repairs_natural_language_planner_clarification_to_structured_action(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = _context(workspace)
    for tool in build_default_codex_tools():
        context.application_context.tools.register(tool)
    llm = _SequenceLLM(
        [
            "当然可以，不过我需要文件名称、位置和内容。",
            '{"kind":"respond","instruction":"可以，不过我还需要文件名或路径，以及是否需要初始内容。","clarification_required":true,"direct_output":true}',
        ]
    )
    context.application_context.llm_client = llm
    context.application_context.llm_model = "test-model"

    result = CodexAgentLoop(context).run("可以为我创建一个文件吗？")

    assert result.status == "needs_clarification"
    assert result.task.actions[-1].kind == "respond"
    assert result.task.actions[-1].metadata.get("clarification_required") is True
    assert "文件名" in result.final_output or "路径" in result.final_output


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
        CodexAgentLoop(context).run("please inspect the note for me")

    assert exc_info.value.code == "PLANNER_NORMALIZATION_FAILED"
    assert "tool_name 'missing_tool' is not in available tools" in exc_info.value.detail
    assert '"tool_name": "missing_tool"' in exc_info.value.detail
