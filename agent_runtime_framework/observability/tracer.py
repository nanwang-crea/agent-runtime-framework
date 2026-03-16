from __future__ import annotations

from typing import Protocol

from agent_runtime_framework.observability.events import RunEvent


class RunObserver(Protocol):
    def record(self, event: RunEvent) -> None: ...


class InMemoryRunObserver:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def record(self, event: RunEvent) -> None:
        self.events.append(event)
