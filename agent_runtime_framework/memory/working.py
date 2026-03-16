from __future__ import annotations


class WorkingMemory:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def get(self, key: str):
        return self._values.get(key)

    def set(self, key: str, value: object) -> None:
        self._values[key] = value

    def clear(self) -> None:
        self._values.clear()
