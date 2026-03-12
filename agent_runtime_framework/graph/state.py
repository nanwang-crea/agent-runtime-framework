from __future__ import annotations

from pydantic import BaseModel, Field


class BaseState(BaseModel):
    step_count: int = 0
    current_node: str | None = None
    execution_trace: list[str] = Field(default_factory=list)
    routing_history: list[dict[str, str]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    status: str = "running"
    done: bool = False
    last_node: str | None = None
    last_observation: str | None = None
    termination_reason: str | None = None

    def add_trace(self, node_name: str) -> None:
        self.execution_trace.append(node_name)

    def add_note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    def record_route(self, source: str, next_node: str, reason: str) -> None:
        self.routing_history.append(
            {
                "source": source,
                "next_node": next_node,
                "reason": reason,
            }
        )
