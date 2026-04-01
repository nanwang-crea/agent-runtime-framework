from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime_framework.agent_tools.models import AgentToolSpec


@dataclass(slots=True)
class AgentToolRegistry:
    _tools: dict[str, AgentToolSpec] = field(default_factory=dict)

    def register(self, spec: AgentToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> AgentToolSpec | None:
        return self._tools.get(str(name).strip())

    def list(self) -> list[AgentToolSpec]:
        return [self._tools[key] for key in sorted(self._tools)]
