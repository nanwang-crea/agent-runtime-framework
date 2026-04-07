from __future__ import annotations

from dataclasses import dataclass

from agent_runtime_framework.api.presenters.response_builder import ApiResponseBuilder
from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState


@dataclass(slots=True)
class SessionService:
    runtime_state: ApiRuntimeState
    response_builder: ApiResponseBuilder

    def get_session(self) -> dict[str, Any]:
        return self.response_builder.session_snapshot()
