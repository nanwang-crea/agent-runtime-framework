from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SessionTurn:
    role: str
    content: str


@dataclass(slots=True)
class SessionState:
    session_id: str
    turns: list[SessionTurn] = field(default_factory=list)
    focused_capability: str | None = None

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append(SessionTurn(role=role, content=content))
