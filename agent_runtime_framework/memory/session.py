from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from agent_runtime_framework.resources import ResourceRef


@dataclass(slots=True)
class SessionSnapshot:
    focused_resources: list[ResourceRef] = field(default_factory=list)
    last_summary: str | None = None


class SessionMemory(Protocol):
    def snapshot(self) -> SessionSnapshot: ...

    def remember_focus(self, focused_resources: list[ResourceRef], *, summary: str | None = None) -> None: ...


class InMemorySessionMemory:
    def __init__(self) -> None:
        self._snapshot = SessionSnapshot()

    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            focused_resources=list(self._snapshot.focused_resources),
            last_summary=self._snapshot.last_summary,
        )

    def remember_focus(self, focused_resources: list[ResourceRef], *, summary: str | None = None) -> None:
        self._snapshot = SessionSnapshot(
            focused_resources=list(focused_resources),
            last_summary=summary,
        )
