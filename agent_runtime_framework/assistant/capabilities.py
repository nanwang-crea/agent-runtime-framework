from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent_runtime_framework.applications import ApplicationRunner, ApplicationSpec


CapabilityRunner = Callable[[str, Any, Any], str]


@dataclass(slots=True)
class CapabilitySpec:
    name: str
    runner: CapabilityRunner
    source: str
    description: str = ""
    safety_level: str = "local"
    input_contract: dict[str, Any] = field(default_factory=dict)


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        self._capabilities[spec.name] = spec

    def register_application(self, name: str, spec: ApplicationSpec) -> None:
        def _runner(user_input: str, context: Any, session: Any) -> str:
            result = ApplicationRunner(spec, context.application_context).run(user_input)
            return result.final_answer

        self.register(
            CapabilitySpec(
                name=name,
                runner=_runner,
                source="application",
                description=f"Application capability: {name}",
                safety_level="application",
            )
        )

    def register_skill_registry(self, skills: Any) -> None:
        for skill_name in skills.names():
            spec = skills.get(skill_name)
            if spec is None:
                continue
            runner = spec.runner or (lambda user_input, context, session, _name=skill_name: f"skill:{_name}")
            self.register(
                CapabilitySpec(
                    name=f"skill:{skill_name}",
                    runner=runner,
                    source="skill",
                    description=spec.description,
                    safety_level="skill",
                    input_contract={"trigger_phrases": list(spec.trigger_phrases)},
                )
            )

    def register_mcp_provider(self, provider: Any) -> None:
        for tool in provider.list_tools():
            runner = tool.runner or (lambda user_input, context, session, _name=tool.name: f"mcp:{_name}")
            self.register(
                CapabilitySpec(
                    name=f"mcp:{tool.name}",
                    runner=runner,
                    source="mcp",
                    description=tool.description,
                    safety_level=tool.safety_level,
                    input_contract=dict(tool.input_schema),
                )
            )

    def get(self, name: str) -> CapabilitySpec | None:
        return self._capabilities.get(name)

    def require(self, name: str) -> CapabilitySpec:
        capability = self.get(name)
        if capability is None:
            raise KeyError(f"unknown capability: {name}")
        return capability

    def names(self) -> list[str]:
        return list(self._capabilities.keys())
