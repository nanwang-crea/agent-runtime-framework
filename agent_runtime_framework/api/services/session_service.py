from __future__ import annotations

from dataclasses import dataclass

from agent_runtime_framework.api.responses.session_responses import SessionResponseFactory
from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState


@dataclass(slots=True)
class SessionService:
    runtime_state: ApiRuntimeState
    session_responses: SessionResponseFactory

    def get_session(self) -> dict[str, Any]:
        return self.session_responses.session_snapshot()
