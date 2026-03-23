from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime_framework.applications import ApplicationContext, create_desktop_content_application
from agent_runtime_framework.assistant import (
    AgentLoop,
    ApprovalManager,
    AssistantContext,
    AssistantSession,
    CapabilityRegistry,
    SkillRegistry,
    create_conversation_capability,
)
from agent_runtime_framework.assistant.checkpoints import InMemoryCheckpointStore
from agent_runtime_framework.assistant.conversation import stream_conversation_reply
from agent_runtime_framework.assistant.session import ExecutionPlan, PlannedAction
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.models import (
    CodexCliDriver,
    InMemoryCredentialStore,
    ModelRegistry,
    ModelRouter,
    OpenAICompatibleDriver,
)
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.demo.model_center import ModelCenterService, ModelCenterStore
from agent_runtime_framework.core.errors import AppError


@dataclass(slots=True)
class DemoAssistantApp:
    workspace: Path
    context: AssistantContext
    model_registry: ModelRegistry
    model_router: ModelRouter
    model_center: ModelCenterService
    _pending_tokens: dict[str, Any]
    _run_history: list[dict[str, Any]]

    def chat(self, message: str) -> dict[str, Any]:
        try:
            result = AgentLoop(self.context).run(message)
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
        yield {"type": "status", "status": {"phase": "routing", "label": "正在选择能力"}}
        capability_name = AgentLoop(self.context)._select_capability(message, session)
        if capability_name == "conversation":
            yield from self._stream_conversation(message, session)
            return
        yield {"type": "status", "status": {"phase": "execution", "label": f"正在执行 {capability_name}"}}
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
        plan = ExecutionPlan(
            goal=message,
            steps=[PlannedAction(capability_name="conversation", instruction=message, status="in_progress")],
        )
        session.plan_history.append(plan)
        yield {"type": "status", "status": {"phase": "conversation", "label": "正在生成回复"}}
        yield {
            "type": "step",
            "step": {
                "name": "conversation",
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
        plan.steps[0].status = "completed"
        plan.steps[0].observation = final_answer
        session.focused_capability = "conversation"
        source = str(diagnostics.get("source") or "fallback")
        reason = str(diagnostics.get("reason") or "")
        status = "completed" if source == "model" else "fallback"
        payload = {
            "status": "completed",
            "final_answer": final_answer,
            "capability_name": "conversation",
            "execution_trace": [
                {
                    "name": "conversation",
                    "status": status,
                    "detail": f"source={source}; reason={reason}" if reason else f"source={source}",
                }
            ],
            "approval_request": None,
            "resume_token_id": None,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "memory": self.memory_payload(),
            "workspace": str(self.workspace),
        }
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
        result = AgentLoop(self.context).resume(token, approved=approved)
        payload = self._result_payload(result)
        self._record_run(payload, prompt=f"approval:{'approve' if approved else 'reject'}")
        return payload

    def replay(self, run_id: str) -> dict[str, Any]:
        result = AgentLoop(self.context).replay(run_id)
        payload = self._result_payload(result)
        self._record_run(payload, prompt=f"replay:{run_id}")
        return payload

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
        session = self.context.session
        if session is None:
            return []
        return [
            {
                "plan_id": plan.plan_id,
                "goal": plan.goal,
                "steps": [
                    {
                        "capability_name": step.capability_name,
                        "instruction": step.instruction,
                        "status": step.status,
                        "observation": step.observation,
                    }
                    for step in plan.steps
                ],
            }
            for plan in session.plan_history
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
        return {
            "status": result.status,
            "run_id": result.run_id,
            "plan_id": result.plan_id,
            "final_answer": result.final_answer,
            "capability_name": result.capability_name,
            "execution_trace": list(result.execution_trace),
            "approval_request": approval_request,
            "resume_token_id": resume_token_id,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
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
        self._run_history = [item for item in self._run_history if item.get("run_id") != run_id]
        self._run_history.insert(0, entry)
        self._run_history = self._run_history[:40]

    def _error_payload(self, exc: Exception) -> dict[str, Any]:
        error = self._normalize_error(exc)
        return {
            "status": "error",
            "final_answer": error.message,
            "capability_name": "",
            "execution_trace": [
                {
                    "name": error.stage or "run",
                    "status": "error",
                    "detail": f"{error.code}: {error.message}",
                }
            ],
            "approval_request": None,
            "resume_token_id": None,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "error": error.as_dict(),
            "workspace": str(self.workspace),
        }

    def _normalize_error(self, exc: Exception) -> AppError:
        if isinstance(exc, AppError):
            return exc
        if isinstance(exc, FileNotFoundError):
            return AppError(
                code="RESOURCE_NOT_FOUND",
                message="未找到目标资源。",
                detail=str(exc),
                stage="resolve",
                retriable=True,
                suggestion="请检查路径或文件名是否正确。",
            )
        if isinstance(exc, IsADirectoryError):
            return AppError(
                code="RESOURCE_IS_DIRECTORY",
                message="目标是目录，当前操作只接受文件。",
                detail=str(exc),
                stage="execute",
                retriable=True,
                suggestion="可以先列出目录内容，或指定目录下的某个文件。",
            )
        if isinstance(exc, NotADirectoryError):
            return AppError(
                code="RESOURCE_NOT_DIRECTORY",
                message="目标不是目录，无法执行目录操作。",
                detail=str(exc),
                stage="execute",
                retriable=True,
                suggestion="请改为读取文件，或重新指定目录。",
            )
        if isinstance(exc, ValueError) and "outside allowed roots" in str(exc):
            return AppError(
                code="RESOURCE_OUTSIDE_WORKSPACE",
                message="目标超出了当前工作区范围。",
                detail=str(exc),
                stage="resolve",
                retriable=False,
                suggestion="请只操作当前工作区内的文件或目录。",
            )
        return AppError(
            code="INTERNAL_ERROR",
            message="处理请求时发生了未预期错误。",
            detail=f"{type(exc).__name__}: {exc}",
            stage="run",
            retriable=False,
            suggestion="可以重试一次；如果持续出现，请检查后端日志。",
        )

    def _resource_payload(self, resource: Any) -> dict[str, Any]:
        return {
            "resource_id": str(getattr(resource, "resource_id", "")),
            "kind": str(getattr(resource, "kind", "")),
            "location": str(getattr(resource, "location", "")),
            "title": str(getattr(resource, "title", "")),
        }


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
        index_memory=InMemoryIndexMemory(),
        policy=SimpleDesktopPolicy(),
        tools=ToolRegistry(),
        config={"default_directory": str(workspace_path)},
        services={
            "model_registry": model_registry,
            "model_router": model_router,
        },
    )
    context = AssistantContext(
        application_context=app_context,
        capabilities=CapabilityRegistry(),
        skills=SkillRegistry(),
        services={"approval_manager": ApprovalManager()},
        session=AssistantSession(session_id=str(uuid4())),
    )
    context.services["checkpoint_store"] = InMemoryCheckpointStore()
    context.capabilities.register(create_conversation_capability())
    context.capabilities.register_application("desktop_content", create_desktop_content_application())
    app = DemoAssistantApp(
        workspace=workspace_path,
        context=context,
        model_registry=model_registry,
        model_router=model_router,
        model_center=model_center,
        _pending_tokens={},
        _run_history=[],
    )
    app.model_center.load()
    return app
