from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from agent_runtime_framework.api.presenters.app_payloads import build_result_payload
from agent_runtime_framework.api.presenters.error_adapter import error_payload
from agent_runtime_framework.api.presenters.view_state import (
    build_context_payload,
    build_memory_payload,
    build_plan_history_payload,
    build_run_history_payload,
    build_session_payload,
)
from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState
from agent_runtime_framework.errors import log_app_error
from agent_runtime_framework.sandbox import resolve_sandbox

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ApiResponseBuilder:
    runtime_state: ApiRuntimeState

    def session_payload(self) -> dict[str, Any]:
        return build_session_payload(self.runtime_state.context.session)

    def memory_payload(self) -> dict[str, Any]:
        return build_memory_payload(
            session=self.runtime_state.context.session,
            session_memory=self.runtime_state.context.application_context.session_memory,
        )

    def plan_history_payload(self) -> list[dict[str, Any]]:
        return build_plan_history_payload(self.runtime_state._task_history)

    def run_history_payload(self) -> list[dict[str, Any]]:
        return build_run_history_payload(self.runtime_state._run_history)

    def context_payload(self) -> dict[str, Any]:
        return build_context_payload(
            workspace=str(self.runtime_state.workspace),
            active_agent=self.runtime_state._active_agent,
            active_persona=self.runtime_state.active_persona_name(),
            available_profiles=self.runtime_state.available_profiles,
            available_workspaces=self.runtime_state._available_workspaces,
            sandbox_payload=resolve_sandbox(self.runtime_state.context).to_payload(),
        )

    def session_snapshot(self) -> dict[str, Any]:
        return {
            "workspace": str(self.runtime_state.workspace),
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "run_history": self.run_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
        }

    def error_payload(self, exc: Exception) -> dict[str, Any]:
        error, payload = error_payload(
            exc=exc,
            workspace=str(self.runtime_state.workspace),
            active_agent=self.runtime_state._active_agent,
            route_decision=self.runtime_state._last_route_decision,
            session_payload=self.session_payload(),
            plan_history=self.plan_history_payload(),
            run_history=self.run_history_payload(),
            memory_payload=self.memory_payload(),
            context_payload=self.context_payload(),
        )
        log_app_error(logger, error, exc=exc, event="api_runtime_error")
        return payload

    def result_payload(self, result: Any) -> dict[str, Any]:
        return build_result_payload(
            result,
            route_decision=self.runtime_state._last_route_decision,
            session_payload=self.session_payload,
            plan_history_payload=self.plan_history_payload,
            run_history_payload=self.run_history_payload,
            memory_payload=self.memory_payload,
            context_payload=self.context_payload,
            workspace=str(self.runtime_state.workspace),
        )
