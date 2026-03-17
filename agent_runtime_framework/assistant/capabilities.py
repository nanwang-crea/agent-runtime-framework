from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent_runtime_framework.applications import ApplicationRunner, ApplicationSpec


CapabilityRunner = Callable[[str, Any, Any], str]


@dataclass(slots=True)
class CapabilitySpec:
    name: str
    runner: CapabilityRunner
    source: str


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        self._capabilities[spec.name] = spec

    def register_application(self, name: str, spec: ApplicationSpec) -> None:
        def _runner(user_input: str, context: Any, session: Any) -> str:
            result = ApplicationRunner(spec, context.application_context).run(user_input)
            return result.final_answer

        self.register(CapabilitySpec(name=name, runner=_runner, source="application"))

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
                )
            )

    def register_mcp_provider(self, provider: Any) -> None:
        for capability_name, runner in provider.capabilities().items():
            self.register(
                CapabilitySpec(
                    name=f"mcp:{capability_name}",
                    runner=runner,
                    source="mcp",
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
