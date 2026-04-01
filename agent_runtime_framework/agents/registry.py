from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime_framework.agents.definitions import AgentDefinition


@dataclass(slots=True)
class AgentRegistry:
    _agents: dict[str, AgentDefinition] = field(default_factory=dict)

    def register(self, definition: AgentDefinition) -> None:
        if definition.agent_id in self._agents:
            raise ValueError(f"duplicate agent: {definition.agent_id}")
        self._agents[definition.agent_id] = definition

    def register_many(self, definitions: list[AgentDefinition]) -> None:
        for definition in definitions:
            self.register(definition)

    def get(self, agent_id: str) -> AgentDefinition | None:
        return self._agents.get(str(agent_id).strip())

    def require(self, agent_id: str) -> AgentDefinition:
        definition = self.get(agent_id)
        if definition is None:
            raise KeyError(f"unknown agent: {agent_id}")
        return definition

    def list(self) -> list[AgentDefinition]:
        return [self._agents[key] for key in sorted(self._agents)]
