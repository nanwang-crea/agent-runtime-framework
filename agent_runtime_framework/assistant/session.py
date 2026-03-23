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
    confirmed_steps: set[tuple[str, int]] = field(default_factory=set)

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append(AssistantTurn(role=role, content=content))

    def mark_step_confirmed(self, plan_id: str, step_index: int) -> None:
        self.confirmed_steps.add((plan_id, step_index))

    def consume_step_confirmation(self, plan_id: str, step_index: int) -> bool:
        key = (plan_id, step_index)
        if key not in self.confirmed_steps:
            return False
        self.confirmed_steps.remove(key)
        return True


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
