from __future__ import annotations

from typing import Protocol


class IndexMemory(Protocol):
    def get(self, key: str): ...

    def put(self, key: str, value: object) -> None: ...


class InMemoryIndexMemory:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def get(self, key: str):
        return self._values.get(key)

    def put(self, key: str, value: object) -> None:
        self._values[key] = value
