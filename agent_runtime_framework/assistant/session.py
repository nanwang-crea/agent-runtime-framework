from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AssistantTurn:
    role: str
    content: str


@dataclass(slots=True)
class AssistantSession:
    session_id: str
    turns: list[AssistantTurn] = field(default_factory=list)
    focused_capability: str | None = None

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append(AssistantTurn(role=role, content=content))
