from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime_framework.agents import AgentRegistry, builtin_agent_definitions
from agent_runtime_framework.agents.workspace_backend import WorkspaceContext, build_default_workspace_tools
from agent_runtime_framework.agents.workspace_backend.personas import resolve_runtime_persona
from agent_runtime_framework.applications import ApplicationContext
from agent_runtime_framework.assistant.session import AssistantSession
from agent_runtime_framework.memory import InMemorySessionMemory
from agent_runtime_framework.models import (
    CodexCliDriver,
    InMemoryCredentialStore,
    ModelRegistry,
    ModelRouter,
    OpenAICompatibleDriver,
)
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.sandbox import SandboxConfig, resolve_sandbox
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.demo.compat_subtask_runner import CompatSubtaskRunner
from agent_runtime_framework.demo.model_center import ModelCenterService, ModelCenterStore
from agent_runtime_framework.demo.run_lifecycle import RunLifecycleService
from agent_runtime_framework.demo.workflow_branch_orchestrator import WorkflowBranchOrchestrator
from agent_runtime_framework.demo.runtime_factory import DemoRuntimeFactory
from agent_runtime_framework.core.errors import AppError, log_app_error, normalize_app_error
from agent_runtime_framework.workflow import AgentGraphRuntime, GraphExecutionRuntime, RootGraphRuntime, analyze_goal
from agent_runtime_framework.workflow.routing_runtime import RuntimePayload, RootGraphPayload
from agent_runtime_framework.workflow.context_assembly import WorkflowRuntimeContext, build_runtime_context
from agent_runtime_framework.workflow.persistence import WorkflowPersistenceStore
from agent_runtime_framework.workflow.conversation import build_conversation_messages

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class DemoAssistantApp:
    workspace: Path
    context: WorkspaceContext
    _pending_workflow_clarification: dict[str, Any] | None
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
    agent_registry: AgentRegistry
    _workflow_store: WorkflowPersistenceStore
    _compat_subtask_runner: CompatSubtaskRunner

    def chat(self, message: str) -> dict[str, Any]:
        try:
            self._ensure_session()
            payload = self._run_workflow(message)
            return payload
        except Exception as exc:
            return self._error_payload(exc)


    def _ensure_session(self) -> AssistantSession:
        session = self.context.session
        if session is None:
            session = AssistantSession(session_id=str(uuid4()))
            self.context.session = session
        return session

    def _analyze_workflow_goal(self, message: str, context: WorkflowRuntimeContext) -> Any:
        return analyze_goal(message, context=context)

    def _mark_route_decision(self, route: str, source: str) -> None:
        self._last_route_decision = {"route": route, "source": source}

    def _has_pending_clarification(self) -> bool:
        return self._pending_workflow_clarification is not None

    def _get_pending_workflow_clarification(self) -> dict[str, Any] | None:
        return self._pending_workflow_clarification

    def _set_pending_workflow_clarification(self, payload: dict[str, Any] | None) -> None:
        self._pending_workflow_clarification = payload

    def _run_workflow(self, message: str) -> RuntimePayload:
        runtime = self._build_routing_runtime()
        return runtime.run(message)


    def stream_chat(self, message: str, *, chunk_size: int = 24):
        yield {"type": "start", "message": message}
        self._ensure_session()
        yield {"type": "status", "status": {"phase": "routing", "label": "正在规划下一步动作"}}
        yield {"type": "status", "status": {"phase": "execution", "label": "正在执行工作流"}}
        try:
            payload = self._build_routing_runtime().run(message)
        except Exception as exc:
            payload = self._error_payload(exc)
            yield {"type": "error", "error": dict(payload.get("error") or {})}
            return
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

    def approve(self, token_id: str, approved: bool) -> dict[str, Any]:
        return self._build_run_lifecycle().approve(token_id, approved)

    def replay(self, run_id: str) -> dict[str, Any]:
        return self._build_run_lifecycle().replay(run_id)


    def context_payload(self) -> dict[str, Any]:
        return {
            "active_agent": self._active_agent,
            "active_persona": self._active_persona_name(),
            "available_agents": [definition.to_payload() for definition in self.agent_registry.list()],
            "active_workspace": str(self.workspace),
            "available_workspaces": list(dict.fromkeys([str(self.workspace), *self._available_workspaces])),
            "sandbox": resolve_sandbox(self.context).to_payload(),
        }

    def switch_context(self, *, agent_profile: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        if agent_profile:
            if self.agent_registry.get(agent_profile) is None:
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


    def _active_persona_name(self) -> str:
        session = self.context.session
        if session is not None and session.active_persona:
            return session.active_persona
        return resolve_runtime_persona(self.context).name

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


    def _build_runtime_factory(self) -> DemoRuntimeFactory:
        return DemoRuntimeFactory(self)

    def _workflow_runtime_context(self) -> WorkflowRuntimeContext:
        return build_runtime_context(
            application_context=self.context.application_context,
            workspace_context=self.context,
            workspace_root=str(self.workspace),
        )


    def _build_run_lifecycle(self) -> RunLifecycleService:
        return self._build_runtime_factory().build_run_lifecycle()

    def _build_workflow_branch_orchestrator(self) -> WorkflowBranchOrchestrator:
        return self._build_runtime_factory().build_workflow_branch_orchestrator()

    def _build_routing_runtime(self) -> RootGraphRuntime:
        return self._build_runtime_factory().build_routing_runtime()

    def _build_agent_graph_runtime(self) -> AgentGraphRuntime:
        return self._build_runtime_factory().build_agent_graph_runtime()

    def _build_graph_execution_runtime(self) -> GraphExecutionRuntime:
        return self._build_runtime_factory().build_graph_execution_runtime()

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

    @staticmethod
    def _compact_text(value: str, *, limit: int = 200) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()}...[已截断]"

    def _record_run(self, payload: dict[str, Any], prompt: str) -> None:
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
        detail = f"{type(exc).__name__}: {exc}"
        if "llm_unavailable" in detail:
            return normalize_app_error(
                exc,
                code="MODEL_UNAVAILABLE",
                message=str(exc),
                stage="conversation_response",
                retriable=False,
                suggestion="请先在前端“模型 / 配置”中为 conversation 配置可用模型。",
                context={**base_context, "exception_type": type(exc).__name__},
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
    for tool in build_default_workspace_tools():
        app_context.tools.register(tool)
    agent_registry = AgentRegistry()
    agent_registry.register_many(builtin_agent_definitions())
    context = WorkspaceContext(
        application_context=app_context,
        services={},
        session=AssistantSession(session_id=str(uuid4())),
    )
    app = DemoAssistantApp(
        workspace=workspace_path,
        context=context,
        model_registry=model_registry,
        model_router=model_router,
        model_center=model_center,
        _pending_tokens={},
        _run_history=[],
        _task_history=[],
        _run_inputs={},
        _last_route_decision=None,
        _pending_workflow_clarification=None,
        _active_agent="workspace",
        _available_workspaces=[str(workspace_path)],
        agent_registry=agent_registry,
        _workflow_store=WorkflowPersistenceStore(workspace_path / ".arf" / "workflow-runs.json"),
        _compat_subtask_runner=CompatSubtaskRunner(),
    )
    app.model_center.load()
    return app
