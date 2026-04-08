from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agent_runtime_framework.api.state.session_state import SessionState
from agent_runtime_framework.models import ModelRegistry, ModelRouter
from agent_runtime_framework.workflow.context.runtime_context import WorkflowRuntimeContext, build_runtime_context
from agent_runtime_framework.workflow.state.persistence import WorkflowPersistenceStore
from agent_runtime_framework.workflow.workspace import WorkspaceContext

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
    _task_history: list[Any]
    _run_inputs: dict[str, str]
    _last_route_decision: dict[str, str] | None
    _pending_workflow_interaction: dict[str, Any] | None
    _available_workspaces: list[str]
    _workflow_store: WorkflowPersistenceStore

    def ensure_session(self) -> SessionState:
        session = self.context.session
        if session is None:
            session = SessionState(session_id=str(uuid4()))
            self.context.session = session
        return session

    def workflow_runtime_context(self, *, process_sink: Any = None) -> WorkflowRuntimeContext:
        return build_runtime_context(
            application_context=self.context.application_context,
            workspace_context=self.context,
            workspace_root=str(self.workspace),
            process_sink=process_sink,
        )

    def record_run(self, payload: dict[str, Any], prompt: str) -> None:
        run_id = str(payload.get("run_id") or "").strip()
        if run_id:
            self._run_inputs[run_id] = prompt
