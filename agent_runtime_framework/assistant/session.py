from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(slots=True)
class AssistantTurn:
    role: str
    content: str


@dataclass(slots=True)
class AssistantSession:
    session_id: str
    turns: list[AssistantTurn] = field(default_factory=list)
    focused_capability: str | None = None
    plan_history: list["ExecutionPlan"] = field(default_factory=list)

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append(AssistantTurn(role=role, content=content))


@dataclass(slots=True)
class PlannedAction:
    capability_name: str
    instruction: str
    status: str = "pending"
    observation: str | None = None


@dataclass(slots=True)
class ExecutionPlan:
    goal: str
    steps: list[PlannedAction]
    plan_id: str = field(default_factory=lambda: str(uuid4()))
