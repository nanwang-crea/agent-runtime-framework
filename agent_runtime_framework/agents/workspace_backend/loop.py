from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.approval import ApprovalRequest, ResumeToken
from agent_runtime_framework.assistant.session import AssistantSession
from agent_runtime_framework.memory import MemoryRecord
from agent_runtime_framework.tools import ToolCall, execute_tool_call

from agent_runtime_framework.agents.workspace_backend.models import WorkspaceAction, WorkspaceActionResult, WorkspaceTask, TaskIntent, TaskState
from agent_runtime_framework.agents.workspace_backend.planner import infer_task_intent, plan_next_workspace_action
from agent_runtime_framework.agents.workspace_backend.runtime import WorkspaceSessionRuntime

_PENDING_CLARIFICATION_KEY = "workspace_backend:pending_clarification"


@dataclass(slots=True)
class WorkspaceContext:
    application_context: ApplicationContext
    services: dict[str, Any] = field(default_factory=dict)
    session: AssistantSession | None = None


@dataclass(slots=True)
class WorkspaceAgentLoopResult:
    status: str
    final_output: str
    task: WorkspaceTask
    action_kind: str = ""
    approval_request: ApprovalRequest | None = None
    resume_token: ResumeToken | None = None
    run_id: str = ""


class WorkspaceAgentLoop:
    _result_type = WorkspaceAgentLoopResult

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self._pending_clarifications: dict[str, WorkspaceTask] = {}

    def run(self, user_input: str) -> WorkspaceAgentLoopResult:
        session = self._require_session()
        session.add_turn("user", user_input)
        task = self._build_task(user_input, session)
        self._runtime().on_task_started(task)
        return self._execute_task(task, session)

    def resume(self, token: ResumeToken, *, approved: bool) -> WorkspaceAgentLoopResult:
        raise RuntimeError("approval resume is not implemented in the simplified workspace backend")

    def has_pending_clarification(self, session: AssistantSession | None = None) -> bool:
        active_session = session or self.context.session
        if active_session is not None and active_session.session_id in self._pending_clarifications:
            return True
        stored = self._stored_pending_clarification()
        return stored is not None

    def _require_session(self) -> AssistantSession:
        session = self.context.session
        if session is None:
            session = AssistantSession(session_id=str(uuid4()))
            self.context.session = session
        return session

    def _runtime(self) -> WorkspaceSessionRuntime:
        runtime = self.context.services.get("session_runtime") if isinstance(self.context.services, dict) else None
        if isinstance(runtime, WorkspaceSessionRuntime):
            self.context.application_context.services["tool_runtime"] = runtime
            return runtime
        runtime = WorkspaceSessionRuntime()
        self.context.services["session_runtime"] = runtime
        self.context.application_context.services["tool_runtime"] = runtime
        return runtime

    def _workspace_root(self) -> Path | None:
        root_value = self.context.application_context.config.get("default_directory")
        return Path(str(root_value)).expanduser().resolve() if root_value else None

    def _build_task(self, user_input: str, session: AssistantSession) -> WorkspaceTask:
        pending = self._pending_clarifications.pop(session.session_id, None)
        stored = self._stored_pending_clarification()
        if pending is None and stored is not None:
            pending = stored
            self._clear_stored_pending_clarification()
        goal = user_input
        if pending is not None:
            goal = self._merge_clarification_goal(pending.goal, user_input)
        intent = infer_task_intent(goal, self._workspace_root())
        task = WorkspaceTask(goal=goal, actions=[], task_profile=intent.task_kind, intent=intent, state=TaskState(task_intent=intent))
        return task

    def _execute_task(self, task: WorkspaceTask, session: AssistantSession) -> WorkspaceAgentLoopResult:
        action_kind = ""
        final_output = ""
        for _ in range(8):
            action = self._next_action(task, session)
            if action is None:
                break
            task.actions.append(action)
            action_kind = action.kind
            result = self._execute_action(task, action)
            action.status = result.status
            action.observation = result.final_output
            action.metadata.update(result.metadata)
            self._runtime().record_action(task, action)
            if action.kind == "respond":
                final_output = result.final_output
                status = "needs_clarification" if bool(action.metadata.get("clarification_required")) else "completed"
                task.status = status
                if status == "needs_clarification":
                    self._remember_pending_clarification(session, task)
                else:
                    self._clear_stored_pending_clarification()
                    self._remember_completed_task(task, final_output)
                session.add_turn("assistant", final_output)
                return WorkspaceAgentLoopResult(status=status, final_output=final_output, task=task, action_kind=action_kind, run_id=str(uuid4()))
        task.status = "completed"
        self._remember_completed_task(task, final_output)
        if final_output:
            session.add_turn("assistant", final_output)
        return WorkspaceAgentLoopResult(status="completed", final_output=final_output, task=task, action_kind=action_kind, run_id=str(uuid4()))

    def _next_action(self, task: WorkspaceTask, session: AssistantSession) -> WorkspaceAction | None:
        planner = self.context.services.get("next_action_planner") if isinstance(self.context.services, dict) else None
        if callable(planner):
            tool_names = self.context.application_context.tools.names()
            return planner(task, session, self.context, tool_names)
        return plan_next_workspace_action(task, session, self.context)

    def _execute_action(self, task: WorkspaceTask, action: WorkspaceAction) -> WorkspaceActionResult:
        if action.kind == "respond":
            return WorkspaceActionResult(status="completed", final_output=str(action.instruction or ""))
        if action.kind != "call_tool":
            return WorkspaceActionResult(status="failed", final_output=f"unsupported action kind: {action.kind}")
        tool_name = str(action.metadata.get("tool_name") or "")
        if not tool_name:
            return WorkspaceActionResult(status="failed", final_output="missing tool_name")
        tool = self.context.application_context.tools.require(tool_name)
        arguments = dict(action.metadata.get("arguments") or {})
        result = execute_tool_call(tool, ToolCall(tool_name=tool_name, arguments=arguments), task=task, context=self.context)
        if not result.success and isinstance(result.exception, IsADirectoryError) and tool_name in {"read_workspace_text", "read_workspace_excerpt", "summarize_workspace_text"}:
            fallback_tool = self.context.application_context.tools.require("inspect_workspace_path")
            result = execute_tool_call(fallback_tool, ToolCall(tool_name="inspect_workspace_path", arguments={"path": arguments.get("path", "")}), task=task, context=self.context)
            tool_name = "inspect_workspace_path"
        if not result.success:
            if result.exception is not None:
                raise result.exception
            return WorkspaceActionResult(status="failed", final_output=str(result.error or "tool execution failed"), metadata={"result": {"tool_output": result.output or {}, "tool_error": result.error}})
        output = dict(result.output or {})
        final_output = str(output.get("text") or output.get("content") or output.get("stdout") or output.get("summary") or "")
        return WorkspaceActionResult(status="completed", final_output=final_output, metadata={"tool_name": tool_name, "arguments": arguments, "result": {"tool_output": output}})

    def _remember_pending_clarification(self, session: AssistantSession, task: WorkspaceTask) -> None:
        self._pending_clarifications[session.session_id] = task
        put = getattr(getattr(self.context.application_context, "index_memory", None), "put", None)
        if callable(put):
            put(_PENDING_CLARIFICATION_KEY, {"goal": task.goal, "task_profile": task.task_profile})

    def _stored_pending_clarification(self) -> WorkspaceTask | None:
        get = getattr(getattr(self.context.application_context, "index_memory", None), "get", None)
        if not callable(get):
            return None
        payload = get(_PENDING_CLARIFICATION_KEY)
        if not isinstance(payload, dict):
            return None
        goal = str(payload.get("goal") or "")
        task_profile = str(payload.get("task_profile") or "file_reader")
        return WorkspaceTask(goal=goal, actions=[], task_profile=task_profile)

    def _clear_stored_pending_clarification(self) -> None:
        put = getattr(getattr(self.context.application_context, "index_memory", None), "put", None)
        if callable(put):
            put(_PENDING_CLARIFICATION_KEY, None)

    def _remember_completed_task(self, task: WorkspaceTask, final_output: str) -> None:
        remember = getattr(getattr(self.context.application_context, "index_memory", None), "remember", None)
        if not callable(remember):
            return
        if task.state.resolved_target:
            remember(MemoryRecord(key=f"task:{task.task_id}", text=f"{task.goal}\n{final_output}".strip(), kind="task_conclusion", metadata={"goal": task.goal, "path": task.state.resolved_target}))

    @staticmethod
    def _merge_clarification_goal(goal: str, clarification: str) -> str:
        base = goal.strip()
        detail = clarification.strip()
        if not base:
            return detail
        if not detail:
            return base
        return f"{base}\nUser clarification: {detail}"
