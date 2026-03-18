from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime_framework.applications import ApplicationContext, create_desktop_content_application
from agent_runtime_framework.assistant import (
    AgentLoop,
    AssistantContext,
    AssistantSession,
    CapabilityRegistry,
    SkillRegistry,
    create_conversation_capability,
)
from agent_runtime_framework.assistant.conversation import stream_conversation_reply
from agent_runtime_framework.assistant.session import ExecutionPlan, PlannedAction
from agent_runtime_framework.memory import InMemoryIndexMemory, InMemorySessionMemory
from agent_runtime_framework.models import InMemoryCredentialStore, ModelProfile, ModelRegistry, ModelRouter, OpenAICompatibleProvider
from agent_runtime_framework.policy import SimpleDesktopPolicy
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.tools import ToolRegistry
from agent_runtime_framework.demo.config import DemoConfigStore, config_payload
from agent_runtime_framework.core.errors import AppError


@dataclass(slots=True)
class DemoAssistantApp:
    workspace: Path
    context: AssistantContext
    model_registry: ModelRegistry
    model_router: ModelRouter
    config_store: DemoConfigStore
    _pending_tokens: dict[str, Any]

    def chat(self, message: str) -> dict[str, Any]:
        try:
            result = AgentLoop(self.context).run(message)
            payload = self._result_payload(result)
            if result.resume_token is not None:
                self._pending_tokens[result.resume_token.token_id] = result.resume_token
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
                "memory": self.memory_payload(),
                "approval_request": None,
                "resume_token_id": None,
                "workspace": str(self.workspace),
            }
        result = AgentLoop(self.context).resume(token, approved=approved)
        return self._result_payload(result)

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

    def models_payload(self) -> dict[str, Any]:
        providers: list[dict[str, Any]] = []
        for provider_name in self.model_registry.provider_names():
            providers.append(
                {
                    "provider": provider_name,
                    "authenticated": bool(self.model_registry.auth_session(provider_name) and self.model_registry.auth_session(provider_name).authenticated),
                    "auth_session": self.model_registry.auth_session(provider_name).as_dict() if self.model_registry.auth_session(provider_name) else None,
                    "models": [
                        profile.as_dict()
                        for profile in self.model_registry.list_models(provider_name)
                    ],
                }
            )
        return {
            "providers": providers,
            "routes": self.model_router.routes_payload(),
        }

    def config_payload(self) -> dict[str, Any]:
        return config_payload(self.config_store.load_or_create(), path=self.config_store.path)

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        updated = self.config_store.update(payload)
        self._apply_config(updated)
        return {
            "config": config_payload(updated, path=self.config_store.path),
            "models": self.models_payload(),
        }

    def authenticate_provider(self, provider_name: str, credentials: dict[str, Any]) -> dict[str, Any]:
        session = self.model_registry.authenticate(provider_name, credentials)
        self.config_store.update(
            {
                "providers": {
                    provider_name: {
                        "api_key": str(credentials.get("api_key") or ""),
                        "base_url": str(credentials.get("base_url") or ""),
                    }
                }
            }
        )
        return {
            "auth_session": session.as_dict(),
            "config": self.config_payload(),
            **self.models_payload(),
        }

    def select_model(self, role: str, provider: str, model_name: str) -> dict[str, Any]:
        self.model_router.set_route(role, provider=provider, model_name=model_name)
        self.config_store.update(
            {
                "routes": {
                    role: {
                        "provider": provider,
                        "model_name": model_name,
                    }
                }
            }
        )
        return self.models_payload()

    def _apply_config(self, config: dict[str, Any]) -> None:
        for provider_name, provider_config in (config.get("providers") or {}).items():
            api_key = str((provider_config or {}).get("api_key") or "").strip()
            base_url = str((provider_config or {}).get("base_url") or "").strip()
            if api_key:
                self.model_registry.authenticate(
                    provider_name,
                    {"api_key": api_key, "base_url": base_url},
                )
        for role, route in (config.get("routes") or {}).items():
            provider = str((route or {}).get("provider") or "").strip()
            model_name = str((route or {}).get("model_name") or "").strip()
            if provider and model_name:
                self.model_router.set_route(role, provider=provider, model_name=model_name)

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
            "final_answer": result.final_answer,
            "capability_name": result.capability_name,
            "execution_trace": list(result.execution_trace),
            "approval_request": approval_request,
            "resume_token_id": resume_token_id,
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "memory": self.memory_payload(),
            "workspace": str(self.workspace),
        }

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
    config_store = DemoConfigStore(workspace_path / ".arf_demo_config.json")
    model_registry = ModelRegistry(credential_store=InMemoryCredentialStore())
    model_registry.register_provider(OpenAICompatibleProvider())
    model_registry.register_provider(
        OpenAICompatibleProvider(
            provider_name="dashscope",
            default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            available_models=[
                ModelProfile(
                    provider="dashscope",
                    model_name="qwen3.5-plus",
                    display_name="Qwen 3.5 Plus",
                    cost_level="medium",
                    latency_level="medium",
                    reasoning_level="high",
                    recommended_roles=["conversation", "capability_selector", "planner"],
                ),
                ModelProfile(
                    provider="dashscope",
                    model_name="qwen-plus",
                    display_name="Qwen Plus",
                    cost_level="low",
                    latency_level="low",
                    reasoning_level="medium",
                    recommended_roles=["conversation", "capability_selector"],
                ),
            ],
        )
    )
    model_router = ModelRouter(model_registry)
    loaded_config = config_store.load_or_create(seed=seed_config)
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
        services={},
        session=AssistantSession(session_id=str(uuid4())),
    )
    context.capabilities.register(create_conversation_capability())
    context.capabilities.register_application("desktop_content", create_desktop_content_application())
    app = DemoAssistantApp(
        workspace=workspace_path,
        context=context,
        model_registry=model_registry,
        model_router=model_router,
        config_store=config_store,
        _pending_tokens={},
    )
    app._apply_config(loaded_config)
    return app
