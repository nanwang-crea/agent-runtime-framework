from __future__ import annotations

from dataclasses import dataclass
import logging

from agent_runtime_framework.api.responses.error_payloads import error_payload
from agent_runtime_framework.api.responses.session_responses import SessionResponseFactory
from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState
from agent_runtime_framework.errors import log_app_error

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ErrorResponseFactory:
    runtime_state: ApiRuntimeState
    session_responses: SessionResponseFactory

    def error_payload(self, exc: Exception) -> dict[str, object]:
        error, payload = error_payload(
            exc=exc,
            workspace=str(self.runtime_state.workspace),
            active_agent=self.runtime_state._active_agent,
            route_decision=self.runtime_state._last_route_decision,
            session_payload=self.session_responses.session_payload(),
            plan_history=self.session_responses.plan_history_payload(),
            run_history=self.session_responses.run_history_payload(),
            memory_payload=self.session_responses.memory_payload(),
            context_payload=self.session_responses.context_payload(),
        )
        log_app_error(logger, error, exc=exc, event="api_runtime_error")
        return payload
