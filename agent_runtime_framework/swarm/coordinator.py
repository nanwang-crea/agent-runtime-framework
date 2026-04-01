from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime_framework.swarm.models import SwarmState


@dataclass(slots=True)
class SwarmCoordinator:
    states: dict[str, SwarmState] = field(default_factory=dict)

    def open(self, root_session_id: str) -> SwarmState:
        state = self.states.setdefault(root_session_id, SwarmState(root_session_id=root_session_id, active_session_ids=[root_session_id]))
        return state

    def add_child(self, root_session_id: str, child_session_id: str, *, agent_id: str) -> SwarmState:
        state = self.open(root_session_id)
        if child_session_id not in state.active_session_ids:
            state.active_session_ids.append(child_session_id)
        state.handoffs.append({"child_session_id": child_session_id, "agent_id": agent_id})
        return state
