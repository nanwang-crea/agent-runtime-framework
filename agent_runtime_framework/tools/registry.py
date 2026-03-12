from __future__ import annotations

from agent_runtime_framework.core.specs import ToolSpec


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec] | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def require(self, name: str) -> ToolSpec:
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"unknown tool: {name}")
        return tool

    def names(self) -> list[str]:
        return list(self._tools.keys())
