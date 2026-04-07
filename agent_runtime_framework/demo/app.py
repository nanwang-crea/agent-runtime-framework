from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime_framework.workflow.workspace import WorkspaceContext, resolve_runtime_persona
from agent_runtime_framework.models import (
    ModelRegistry,
    ModelRouter,
)
from agent_runtime_framework.resources import LocalFileResourceRepository
from agent_runtime_framework.sandbox import SandboxConfig, resolve_sandbox
from agent_runtime_framework.demo.model_center import ModelCenterService
from agent_runtime_framework.demo.app_payloads import build_result_payload
from agent_runtime_framework.demo.profiles import get_demo_profile
from agent_runtime_framework.demo.run_history import record_run
from agent_runtime_framework.demo.runtime_factory import DemoRuntimeFactory
from agent_runtime_framework.demo.session_state import DemoSessionState
from agent_runtime_framework.demo.error_adapter import error_payload
from agent_runtime_framework.demo.view_state import (
    build_context_payload,
    build_memory_payload,
    build_plan_history_payload,
    build_run_history_payload,
    build_session_payload,
)
from agent_runtime_framework.errors import log_app_error
from agent_runtime_framework.workflow.routing_runtime import RuntimePayload, RootGraphPayload
from agent_runtime_framework.workflow.context_assembly import WorkflowRuntimeContext, build_runtime_context
from agent_runtime_framework.workflow.persistence import WorkflowPersistenceStore

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
    available_profiles: list[Any]
    _workflow_store: WorkflowPersistenceStore

    def chat(self, message: str) -> dict[str, Any]:
        try:
            self._ensure_session()
            payload = DemoRuntimeFactory(self).build_routing_runtime().run(message)
            return payload
        except Exception as exc:
            return self._error_payload(exc)


    def _ensure_session(self) -> DemoSessionState:
        session = self.context.session
        if session is None:
            session = DemoSessionState(session_id=str(uuid4()))
            self.context.session = session
        return session

    def stream_chat(self, message: str, *, chunk_size: int = 24):
        yield {"type": "start", "message": message}
        self._ensure_session()
        yield {"type": "status", "status": {"phase": "routing", "label": "正在规划下一步动作"}}
        yield {"type": "status", "status": {"phase": "execution", "label": "正在执行工作流"}}
        try:
            payload = DemoRuntimeFactory(self).build_routing_runtime().run(message)
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

    def context_payload(self) -> dict[str, Any]:
        return build_context_payload(
            workspace=str(self.workspace),
            active_agent=self._active_agent,
            active_persona=self._active_persona_name(),
            available_profiles=self.available_profiles,
            available_workspaces=self._available_workspaces,
            sandbox_payload=resolve_sandbox(self.context).to_payload(),
        )

    def switch_context(self, *, agent_profile: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        if agent_profile:
            if get_demo_profile(agent_profile) is None:
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
        return build_session_payload(self.context.session)

    def _workflow_runtime_context(self) -> WorkflowRuntimeContext:
        return build_runtime_context(
            application_context=self.context.application_context,
            workspace_context=self.context,
            workspace_root=str(self.workspace),
        )

    def memory_payload(self) -> dict[str, Any]:
        return build_memory_payload(
            session=self.context.session,
            session_memory=self.context.application_context.session_memory,
        )

    def plan_history_payload(self) -> list[dict[str, Any]]:
        return build_plan_history_payload(self._task_history)

    def run_history_payload(self) -> list[dict[str, Any]]:
        return build_run_history_payload(self._run_history)

    def _error_payload(self, exc: Exception) -> dict[str, Any]:
        error, payload = error_payload(
            exc=exc,
            workspace=str(self.workspace),
            active_agent=self._active_agent,
            route_decision=self._last_route_decision,
            session_payload=self.session_payload(),
            plan_history=self.plan_history_payload(),
            run_history=self.run_history_payload(),
            memory_payload=self.memory_payload(),
            context_payload=self.context_payload(),
        )
        log_app_error(logger, error, exc=exc, event="demo_app_error")
        return payload

    def result_payload(self, result: Any) -> dict[str, Any]:
        return build_result_payload(
            result,
            route_decision=self._last_route_decision,
            session_payload=self.session_payload,
            plan_history_payload=self.plan_history_payload,
            run_history_payload=self.run_history_payload,
            memory_payload=self.memory_payload,
            context_payload=self.context_payload,
            workspace=str(self.workspace),
        )

    def record_run(self, payload: dict[str, Any], prompt: str) -> None:
        self._run_history = record_run(
            payload=payload,
            prompt=prompt,
            run_inputs=self._run_inputs,
            run_history=self._run_history,
        )
