from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from agent_runtime_framework.agents.codex import CodexAction, CodexAgentLoop, CodexContext, build_default_codex_tools, evaluate_codex_output, plan_next_codex_action
from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.conversation import get_route_decision, should_route_to_conversation, stream_conversation_reply
from agent_runtime_framework.assistant.session import AssistantSession
from agent_runtime_framework.memory import InMemorySessionMemory
from agent_runtime_framework.models import (
    CodexCliDriver,
    InMemoryCredentialStore,
    ModelRegistry,
    ModelRouter,
    OpenAICompatibleDriver,
    resolve_model_runtime,
)
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.sandbox import SandboxConfig, resolve_sandbox
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.demo.model_center import ModelCenterService, ModelCenterStore
from agent_runtime_framework.core.errors import AppError, log_app_error, normalize_app_error

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DemoAssistantApp:
    workspace: Path
    context: CodexContext
    loop: CodexAgentLoop
    model_registry: ModelRegistry
    model_router: ModelRouter
    model_center: ModelCenterService
    _pending_tokens: dict[str, Any]
    _run_history: list[dict[str, Any]]
    _task_history: list[Any]
    _run_inputs: dict[str, str]
    _last_route_decision: dict[str, str] | None
    _active_agent: str
    _available_workspaces: list[str]

    def chat(self, message: str) -> dict[str, Any]:
        try:
            if self._active_agent == "qa_only":
                self._last_route_decision = {"route": "conversation", "source": "profile"}
                return self._conversation_payload(message)
            if self.loop.has_pending_clarification(self.context.session):
                self._last_route_decision = {"route": "codex", "source": "clarification"}
            else:
                route_decision = get_route_decision(message, self.context)
                self._last_route_decision = route_decision
                if route_decision["route"] == "conversation":
                    return self._conversation_payload(message)
            self._ensure_codex_planner_available()
            result = self.loop.run(message)
            self._task_history.insert(0, result.task)
            self._task_history = self._task_history[:40]
            payload = self._result_payload(result)
            if result.resume_token is not None:
                self._pending_tokens[result.resume_token.token_id] = result.resume_token
            self._record_run(payload, prompt=message)
            return payload
        except Exception as exc:
            return self._error_payload(exc)

    def stream_chat(self, message: str, *, chunk_size: int = 24):
        yield {"type": "start", "message": message}
        session = self.context.session
        if session is None:
            session = AssistantSession(session_id=str(uuid4()))
            self.context.session = session
        yield {"type": "status", "status": {"phase": "routing", "label": "正在规划下一步动作"}}
        if self._active_agent != "qa_only":
            try:
                self._ensure_codex_planner_available()
            except Exception as exc:
                payload = self._error_payload(exc)
                yield {"type": "error", "error": dict(payload.get("error") or {})}
                return
        if self._should_stream_conversation(message, session):
            yield from self._stream_conversation(message, session)
            return
        yield {"type": "status", "status": {"phase": "execution", "label": "正在执行工作流"}}
        payload = self.chat(message)
        if payload.get("status") == "error":
            yield {"type": "error", "error": dict(payload.get("error") or {})}
            return
        for step in payload.get("execution_trace", []):
            yield {"type": "step", "step": step}
        yield {"type": "memory", "memory": self.memory_payload()}
        final_answer = str(payload.get("final_answer") or "")
        if not final_answer:
            yield {"type": "final", "payload": payload}
            return
        yield {"type": "delta", "delta": final_answer}
        yield {"type": "final", "payload": payload}

    def _stream_conversation(self, message: str, session: AssistantSession):
        session.add_turn("user", message)
        yield {"type": "status", "status": {"phase": "conversation", "label": "正在生成回复"}}
        router_step = self._router_trace_step()
        if router_step is not None:
            yield {"type": "step", "step": router_step}
        yield {
            "type": "step",
            "step": {
                "name": "respond",
                "status": "running",
                "detail": "streaming_response",
            },
        }
        chunks: list[str] = []
        diagnostics: dict[str, str | None] = {"source": "fallback", "reason": "unknown"}
        for chunk in stream_conversation_reply(message, self.context, session, diagnostics=diagnostics):
            if not chunk:
                continue
            chunks.append(chunk)
            yield {"type": "delta", "delta": chunk}
        final_answer = "".join(chunks).strip()
        session.add_turn("assistant", final_answer)
        session.focused_capability = "respond"
        source = str(diagnostics.get("source") or "fallback")
        reason = str(diagnostics.get("reason") or "")
        status = "completed" if source == "model" else "fallback"
        task = type("CodexConversationTask", (), {})()
        task.task_id = str(uuid4())
        task.goal = message
        task.actions = [type("CodexConversationAction", (), {"kind": "respond", "instruction": message, "status": "completed", "observation": final_answer, "metadata": {}})()]
        self._task_history.insert(0, task)
        self._task_history = self._task_history[:40]
        payload = {
            "status": "completed",
            "run_id": str(uuid4()),
            "plan_id": task.task_id,
            "final_answer": final_answer,
            "capability_name": "conversation",
            "execution_trace": self._with_router_trace(
                [
                    {
                        "name": "respond",
                        "status": status,
                        "detail": f"source={source}; reason={reason}" if reason else f"source={source}",
                    }
                ]
            ),
            "approval_request": None,
            "resume_token_id": None,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "workspace": str(self.workspace),
        }
        self._record_run(payload, prompt=message)
        yield {"type": "memory", "memory": payload["memory"]}
        yield {"type": "final", "payload": payload}

    def approve(self, token_id: str, approved: bool) -> dict[str, Any]:
        token = self._pending_tokens.pop(token_id, None)
        if token is None:
            return {
                "status": "missing_token",
                "final_answer": "未找到可恢复的审批请求。",
                "capability_name": "",
                "execution_trace": [],
                "session": self.session_payload(),
                "plan_history": self.plan_history_payload(),
                "run_history": self.run_history_payload(),
                "memory": self.memory_payload(),
                "approval_request": None,
                "resume_token_id": None,
                "workspace": str(self.workspace),
            }
        result = self.loop.resume(token, approved=approved)
        self._task_history.insert(0, result.task)
        self._task_history = self._task_history[:40]
        payload = self._result_payload(result)
        self._record_run(payload, prompt=f"approval:{'approve' if approved else 'reject'}")
        return payload

    def replay(self, run_id: str) -> dict[str, Any]:
        prompt = self._run_inputs.get(run_id)
        if not prompt:
            return {
                "status": "missing_run",
                "final_answer": "未找到可重放的运行记录。",
                "capability_name": "",
                "execution_trace": [],
                "session": self.session_payload(),
                "plan_history": self.plan_history_payload(),
                "run_history": self.run_history_payload(),
                "memory": self.memory_payload(),
                "approval_request": None,
                "resume_token_id": None,
                "workspace": str(self.workspace),
            }
        payload = self.chat(prompt)
        self._record_run(payload, prompt=f"replay:{run_id}")
        return payload

    def context_payload(self) -> dict[str, Any]:
        return {
            "active_agent": self._active_agent,
            "available_agents": [
                {"id": "codex", "label": "Codex Agent", "kind": "agent"},
                {"id": "qa_only", "label": "Q&A", "kind": "chat"},
            ],
            "active_workspace": str(self.workspace),
            "available_workspaces": list(dict.fromkeys([str(self.workspace), *self._available_workspaces])),
            "sandbox": resolve_sandbox(self.context).to_payload(),
        }

    def switch_context(self, *, agent_profile: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        if agent_profile:
            if agent_profile not in {"codex", "qa_only"}:
                raise ValueError(f"unknown agent profile: {agent_profile}")
            self._active_agent = agent_profile
        if workspace:
            next_workspace = Path(workspace).expanduser().resolve()
            if not next_workspace.exists():
                raise FileNotFoundError(next_workspace)
            self.workspace = next_workspace
            self.context.application_context.resource_repository = LocalFileResourceRepository([next_workspace])
            self.context.application_context.config["default_directory"] = str(next_workspace)
            sandbox = self.context.application_context.services.get("sandbox")
            if isinstance(sandbox, SandboxConfig):
                sandbox.workspace_root = next_workspace
                sandbox.writable_roots = [next_workspace]
            self._available_workspaces = list(dict.fromkeys([str(next_workspace), *self._available_workspaces]))
        return {
            "workspace": str(self.workspace),
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
        }

    def session_payload(self) -> dict[str, Any]:
        session = self.context.session
        if session is None:
            return {"session_id": None, "turns": []}
        return {
            "session_id": session.session_id,
            "turns": [
                {"role": turn.role, "content": turn.content}
                for turn in session.turns
            ],
        }

    def memory_payload(self) -> dict[str, Any]:
        snapshot = self.context.application_context.session_memory.snapshot()
        focused_resources = list(snapshot.focused_resources)
        return {
            "focused_resource": self._resource_payload(focused_resources[0]) if focused_resources else None,
            "recent_resources": [self._resource_payload(resource) for resource in focused_resources[:5]],
            "last_summary": snapshot.last_summary,
            "active_capability": self.context.session.focused_capability if self.context.session is not None else None,
        }

    def model_center_payload(self) -> dict[str, Any]:
        return self.model_center.payload()

    def update_model_center(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.model_center.update(payload)

    def run_model_center_action(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.model_center.run_action(action, payload)

    def plan_history_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "plan_id": task.task_id,
                "goal": task.goal,
                "steps": [
                    {
                        "capability_name": action.kind,
                        "instruction": action.instruction,
                        "status": action.status,
                        "observation": self._compact_text(action.observation),
                    }
                    for action in task.actions
                ],
            }
            for task in reversed(self._task_history[:40])
        ]

    def _result_payload(self, result: Any) -> dict[str, Any]:
        approval_request = None
        resume_token_id = None
        if result.approval_request is not None:
            approval_request = {
                "capability_name": result.approval_request.capability_name,
                "instruction": result.approval_request.instruction,
                "reason": result.approval_request.reason,
                "risk_class": result.approval_request.risk_class,
            }
        if result.resume_token is not None:
            resume_token_id = result.resume_token.token_id
        capability_name = result.action_kind
        if result.action_kind == "respond" and result.task.actions:
            last_action = result.task.actions[-1]
            if not bool(last_action.metadata.get("direct_output")):
                capability_name = "conversation"
        return {
            "status": result.status,
            "run_id": result.run_id,
            "plan_id": result.task.task_id,
            "final_answer": result.final_output,
            "capability_name": capability_name,
            "execution_trace": self._with_router_trace(
                [
                    {
                        "name": "evaluator" if bool(action.metadata.get("from_evaluator")) else action.kind,
                        "status": action.status,
                        "detail": self._compact_text(self._trace_detail_for_action(action)),
                    }
                    for action in result.task.actions
                ]
            ),
            "approval_request": approval_request,
            "resume_token_id": resume_token_id,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "workspace": str(self.workspace),
        }

    def run_history_payload(self) -> list[dict[str, Any]]:
        return list(self._run_history[:40])

    def _record_run(self, payload: dict[str, Any], *, prompt: str) -> None:
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return
        entry = {
            "run_id": run_id,
            "status": str(payload.get("status") or ""),
            "capability_name": str(payload.get("capability_name") or ""),
            "prompt": prompt,
            "final_answer_preview": str(payload.get("final_answer") or "")[:160],
        }
        self._run_inputs[run_id] = prompt
        self._run_history = [item for item in self._run_history if item.get("run_id") != run_id]
        self._run_history.insert(0, entry)
        self._run_history = self._run_history[:40]

    def _should_stream_conversation(self, message: str, _session: AssistantSession) -> bool:
        if self._active_agent == "qa_only":
            self._last_route_decision = {"route": "conversation", "source": "profile"}
            return True
        if self.loop.has_pending_clarification(self.context.session):
            self._last_route_decision = {"route": "codex", "source": "clarification"}
            return False
        self._last_route_decision = get_route_decision(message, self.context)
        return self._last_route_decision["route"] == "conversation"

    def _ensure_codex_planner_available(self) -> None:
        if callable(self.context.services.get("next_action_planner")) or callable(self.context.services.get("action_planner")):
            return
        runtime = resolve_model_runtime(self.context.application_context, "planner")
        if runtime is not None:
            return
        route = getattr(self.model_router, "get_route", lambda _role: None)("planner")
        default_route = getattr(self.model_router, "get_route", lambda _role: None)("default")
        raise AppError(
            code="MODEL_UNAVAILABLE",
            message="未配置可用的大模型，Codex Agent 无法规划下一步动作。",
            detail=f"planner model runtime is unavailable; planner_route={route}; default_route={default_route}",
            stage="planner",
            retriable=True,
            suggestion="请先在前端“模型 / 配置”中配置并认证一个 planner 模型。",
            context={
                **self._error_context(),
                "planner_route": route or {},
                "default_route": default_route or {},
            },
        )

    def _compact_text(self, value: Any, *, limit: int = 240) -> str | None:
        if value is None:
            return None
        text = str(value)
        if len(text) <= limit:
            return text
        return f"{text[:limit]}... ({len(text)} chars)"

    def _conversation_payload(self, message: str) -> dict[str, Any]:
        session = self.context.session
        if session is None:
            session = AssistantSession(session_id=str(uuid4()))
            self.context.session = session
        session.add_turn("user", message)
        chunks: list[str] = []
        diagnostics: dict[str, str | None] = {"source": "fallback", "reason": "unknown"}
        for chunk in stream_conversation_reply(message, self.context, session, diagnostics=diagnostics):
            if chunk:
                chunks.append(chunk)
        final_answer = "".join(chunks).strip()
        session.add_turn("assistant", final_answer)
        task = type("CodexConversationTask", (), {})()
        task.task_id = str(uuid4())
        task.goal = message
        task.actions = [type("CodexConversationAction", (), {"kind": "respond", "instruction": message, "status": "completed", "observation": final_answer, "metadata": {}})()]
        self._task_history.insert(0, task)
        self._task_history = self._task_history[:40]
        payload = {
            "status": "completed",
            "run_id": str(uuid4()),
            "plan_id": task.task_id,
            "final_answer": final_answer,
            "capability_name": "conversation",
            "execution_trace": self._with_router_trace([{"name": "respond", "status": "completed", "detail": final_answer}]),
            "approval_request": None,
            "resume_token_id": None,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "workspace": str(self.workspace),
        }
        self._record_run(payload, prompt=message)
        return payload

    def _error_payload(self, exc: Exception) -> dict[str, Any]:
        error = self._normalize_error(exc)
        log_app_error(logger, error, exc=exc, event="demo_app_error")
        return {
            "status": "error",
            "final_answer": error.message,
            "capability_name": "",
            "execution_trace": self._with_router_trace(
                [
                    {
                        "name": error.stage or "run",
                        "status": "error",
                        "detail": f"{error.code}: {error.message}",
                    }
                ]
            ),
            "approval_request": None,
            "resume_token_id": None,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
            "error": error.as_dict(),
            "workspace": str(self.workspace),
        }

    def _normalize_error(self, exc: Exception) -> AppError:
        base_context = self._error_context()
        if isinstance(exc, AppError):
            return normalize_app_error(exc, context=base_context)
        if isinstance(exc, FileNotFoundError):
            return AppError(
                code="RESOURCE_NOT_FOUND",
                message="未找到目标资源。",
                detail=str(exc),
                stage="resolve",
                retriable=True,
                suggestion="请检查路径或文件名是否正确。",
                context=base_context,
            )
        if isinstance(exc, IsADirectoryError):
            return AppError(
                code="RESOURCE_IS_DIRECTORY",
                message="目标是目录，当前操作只接受文件。",
                detail=str(exc),
                stage="execute",
                retriable=True,
                suggestion="可以先列出目录内容，或指定目录下的某个文件。",
                context=base_context,
            )
        if isinstance(exc, NotADirectoryError):
            return AppError(
                code="RESOURCE_NOT_DIRECTORY",
                message="目标不是目录，无法执行目录操作。",
                detail=str(exc),
                stage="execute",
                retriable=True,
                suggestion="请改为读取文件，或重新指定目录。",
                context=base_context,
            )
        if isinstance(exc, ValueError) and "outside allowed roots" in str(exc):
            return AppError(
                code="RESOURCE_OUTSIDE_WORKSPACE",
                message="目标超出了当前工作区范围。",
                detail=str(exc),
                stage="resolve",
                retriable=False,
                suggestion="请只操作当前工作区内的文件或目录。",
                context=base_context,
            )
        return normalize_app_error(
            exc,
            code="INTERNAL_ERROR",
            message="处理请求时发生了未预期错误。",
            stage="run",
            retriable=False,
            suggestion="可以重试一次；如果持续出现，请检查后端日志。",
            context={**base_context, "exception_type": type(exc).__name__},
        )

    def _error_context(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "active_agent": self._active_agent,
            "route": str((self._last_route_decision or {}).get("route") or ""),
            "route_source": str((self._last_route_decision or {}).get("source") or ""),
        }

    def _resource_payload(self, resource: Any) -> dict[str, Any]:
        return {
            "resource_id": str(getattr(resource, "resource_id", "")),
            "kind": str(getattr(resource, "kind", "")),
            "location": str(getattr(resource, "location", "")),
            "title": str(getattr(resource, "title", "")),
        }

    def _with_router_trace(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        router_step = self._router_trace_step()
        if router_step is None:
            return steps
        return [router_step, *steps]

    def _router_trace_step(self) -> dict[str, Any] | None:
        decision = self._last_route_decision
        if not decision:
            return None
        route = str(decision.get("route") or "").strip()
        source = str(decision.get("source") or "").strip()
        if not route:
            return None
        detail = f"route={route}"
        if source:
            detail = f"{detail}; source={source}"
        return {"name": "router", "status": "completed", "detail": detail}

    def _trace_detail_for_action(self, action: Any) -> str:
        base = str(action.observation or action.instruction or "")
        if not bool(action.metadata.get("from_evaluator")):
            return base
        source = str(action.metadata.get("evaluation_source") or "")
        reason = str(action.metadata.get("evaluator_reason") or "")
        detail = "decision=continue"
        if source:
            detail = f"{detail}; source={source}"
        if reason:
            detail = f"{detail}; reason={reason}"
        if base:
            detail = f"{detail}; payload={base}"
        return detail


def create_demo_assistant_app(workspace: str | Path, *, seed_config: dict[str, Any] | None = None) -> DemoAssistantApp:
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.exists():
        raise FileNotFoundError(f"workspace does not exist: {workspace_path}")
    model_center_store = ModelCenterStore(workspace_path / ".arf_demo_config.json")
    model_registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    model_router = ModelRouter(model_registry)
    model_center = ModelCenterService(
        store=model_center_store,
        registry=model_registry,
        router=model_router,
    )
    model_registry.register_driver(OpenAICompatibleDriver())
    model_registry.register_driver(CodexCliDriver())
    model_center.store.load_or_create(seed=seed_config)
    app_context = ApplicationContext(
        resource_repository=LocalFileResourceRepository([workspace_path]),
        session_memory=InMemorySessionMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace_path)},
        services={
            "model_registry": model_registry,
            "model_router": model_router,
            "sandbox": SandboxConfig(
                mode="workspace_write",
                workspace_root=workspace_path,
                writable_roots=[workspace_path],
                allow_network=False,
            ),
        },
    )
    for tool in build_default_codex_tools():
        app_context.tools.register(tool)
    context = CodexContext(
        application_context=app_context,
        services={},
        session=AssistantSession(session_id=str(uuid4())),
    )
    context.services["next_action_planner"] = _build_demo_next_action_planner(workspace_path)
    context.services["output_evaluator"] = evaluate_codex_output
    app = DemoAssistantApp(
        workspace=workspace_path,
        context=context,
        loop=CodexAgentLoop(context),
        model_registry=model_registry,
        model_router=model_router,
        model_center=model_center,
        _pending_tokens={},
        _run_history=[],
        _task_history=[],
        _run_inputs={},
        _last_route_decision=None,
        _active_agent="codex",
        _available_workspaces=[str(workspace_path)],
    )
    app.model_center.load()
    return app


def _build_demo_next_action_planner(workspace_root: Path):
    def _planner(task: Any, session: AssistantSession, context: CodexContext, tool_names: list[str]):
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
        inspect_path = _match_codebase_explanation_target(task.goal, workspace_root)
        if inspect_path and "inspect_workspace_path" in tool_names:
            return CodexAction(
                kind="call_tool",
                instruction=task.goal,
                metadata={
                    "tool_name": "inspect_workspace_path",
                    "arguments": {"path": inspect_path},
                },
            )
        return plan_next_codex_action(task, session, context)

    return _planner


def _match_codebase_explanation_target(user_input: str, workspace_root: Path) -> str | None:
    text = user_input.strip()
    if not text:
        return None
    explanation_markers = ("主要", "功能", "讲些什么", "做什么", "结构", "介绍", "解释", "子文件", "模块")
    if "目录" not in text and "文件夹" not in text:
        return None
    if not any(marker in text for marker in explanation_markers):
        return None
    candidates = re.findall(r"[A-Za-z0-9_./-]+", text)
    for candidate in candidates:
        if candidate in {".", ".."}:
            continue
        target = (workspace_root / candidate).resolve()
        if target.exists() and workspace_root in target.parents and target.is_dir():
            return candidate
    if "当前工作区" in text:
        return ""
    return None
