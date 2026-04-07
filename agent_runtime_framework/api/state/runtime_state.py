from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agent_runtime_framework.api.models.session_state import SessionState
from agent_runtime_framework.api.state.run_history import record_run
from agent_runtime_framework.models import ModelRegistry, ModelRouter
from agent_runtime_framework.workflow.context_assembly import WorkflowRuntimeContext, build_runtime_context
from agent_runtime_framework.workflow.persistence import WorkflowPersistenceStore
from agent_runtime_framework.workflow.workspace import WorkspaceContext, resolve_runtime_persona

if TYPE_CHECKING:
    from agent_runtime_framework.api.services.model_center_service import ModelCenterService


@dataclass(slots=True)
class ApiRuntimeState:
    workspace: Path
    context: WorkspaceContext
    model_registry: ModelRegistry
    model_router: ModelRouter
    model_center: ModelCenterService
    _pending_tokens: dict[str, Any]
    _run_history: list[dict[str, Any]]
    _task_history: list[Any]
    _run_inputs: dict[str, str]
    _last_route_decision: dict[str, str] | None
    _pending_workflow_clarification: dict[str, Any] | None
    _active_agent: str
    _available_workspaces: list[str]
    available_profiles: list[Any]
    _workflow_store: WorkflowPersistenceStore

    def ensure_session(self) -> SessionState:
        session = self.context.session
        if session is None:
            session = SessionState(session_id=str(uuid4()))
            self.context.session = session
        return session

    def active_persona_name(self) -> str:
        session = self.context.session
        if session is not None and session.active_persona:
            return session.active_persona
        return resolve_runtime_persona(self.context).name

    def workflow_runtime_context(self) -> WorkflowRuntimeContext:
        return build_runtime_context(
            application_context=self.context.application_context,
            workspace_context=self.context,
            workspace_root=str(self.workspace),
        )

    def record_run(self, payload: dict[str, Any], prompt: str) -> None:
        self._run_history = record_run(
            payload=payload,
            prompt=prompt,
            run_inputs=self._run_inputs,
            run_history=self._run_history,
        )
