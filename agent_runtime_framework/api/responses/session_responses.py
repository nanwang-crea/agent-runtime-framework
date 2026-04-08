from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime_framework.api.responses.view_payloads import (
    build_context_payload,
    build_memory_payload,
    build_plan_history_payload,
    build_session_payload,
)
from agent_runtime_framework.api.state.runtime_state import ApiRuntimeState


@dataclass(slots=True)
class SessionResponseFactory:
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

    def context_payload(self) -> dict[str, Any]:
        return build_context_payload(
            workspace=str(self.runtime_state.workspace),
            available_workspaces=self.runtime_state._available_workspaces,
        )

    def session_snapshot(self) -> dict[str, Any]:
        return {
            "workspace": str(self.runtime_state.workspace),
            "session": self.session_payload(),
            "plan_history": self.plan_history_payload(),
            "memory": self.memory_payload(),
            "context": self.context_payload(),
        }
