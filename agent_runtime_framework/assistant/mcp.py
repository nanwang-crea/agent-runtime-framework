from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


MCPRunner = Callable[[str, Any, Any], str]


@dataclass(slots=True)
class MCPToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    safety_level: str = "mcp"
    runner: MCPRunner | None = None


class MCPProvider(Protocol):
    def list_tools(self) -> list[MCPToolSpec]: ...


class MCPClient(Protocol):
    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...


class StaticMCPProvider:
    def __init__(self, tools: list[MCPToolSpec]) -> None:
        self._tools = list(tools)

    @classmethod
    def tools(cls, tools: list[dict[str, Any]]) -> "StaticMCPProvider":
        return cls(
            [
                MCPToolSpec(
                    name=str(item["name"]),
                    description=str(item.get("description") or item["name"]),
                    input_schema=dict(item.get("input_schema") or {}),
                    safety_level=str(item.get("safety_level") or "mcp"),
                    runner=item.get("runner"),
                )
                for item in tools
            ]
        )

    def list_tools(self) -> list[MCPToolSpec]:
        return list(self._tools)


class MCPClientAdapter:
    def __init__(self, client: MCPClient) -> None:
        self._client = client

    def list_tools(self) -> list[MCPToolSpec]:
        tools: list[MCPToolSpec] = []
        for item in self._client.list_tools():
            name = str(item["name"])
            tools.append(
                MCPToolSpec(
                    name=name,
                    description=str(item.get("description") or name),
                    input_schema=dict(item.get("input_schema") or {}),
                    safety_level=str(item.get("safety_level") or "mcp"),
                    runner=self._build_runner(name),
                )
            )
        return tools

    def _build_runner(self, tool_name: str) -> MCPRunner:
        def _runner(user_input: str, _context: Any, _session: Any) -> str:
            return self._client.call_tool(tool_name, {"query": user_input})

        return _runner
