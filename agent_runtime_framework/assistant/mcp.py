from __future__ import annotations

from typing import Any, Callable, Protocol


MCPRunner = Callable[[str, Any, Any], str]


class MCPProvider(Protocol):
    def capabilities(self) -> dict[str, MCPRunner]: ...


class StaticMCPProvider:
    def __init__(self, capabilities: dict[str, MCPRunner]) -> None:
        self._capabilities = dict(capabilities)

    def capabilities(self) -> dict[str, MCPRunner]:
        return dict(self._capabilities)
