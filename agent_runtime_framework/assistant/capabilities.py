from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent_runtime_framework.applications import ApplicationRunner, ApplicationSpec
from agent_runtime_framework.core.specs import ToolSpec


CapabilityRunner = Callable[[str, Any, Any], Any]


@dataclass(slots=True)
class CapabilitySpec:
    name: str
    runner: CapabilityRunner
    source: str
    description: str = ""
    safety_level: str = "local"
    input_contract: dict[str, Any] = field(default_factory=dict)
    cost_hint: str = "medium"
    latency_hint: str = "medium"
    risk_class: str = "low"
    dependency_readiness: str = "ready"
    output_type: str = "text"
    execution_mode: str = "direct"


def _risk_class_for_permission(permission_level: str) -> str:
    normalized = permission_level.strip().lower()
    if normalized == "destructive_write":
        return "destructive"
    if normalized in {"safe_write", "write", "execute", "run"}:
        return "high"
    if normalized in {"content_read", "metadata_read", "read"}:
        return "low"
    return "moderate"


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        self._capabilities[spec.name] = spec

    def register_application(self, name: str, spec: ApplicationSpec) -> None:
        def _runner(user_input: str, context: Any, session: Any) -> dict[str, Any]:
            confirmed = bool(context.services.get("step_confirmed"))
            run_context = dict(context.services.get("run_context") or {})
            context.application_context.services["run_context"] = run_context
            context.application_context.services["recent_artifact_ids"] = []
            try:
                result = ApplicationRunner(spec, context.application_context).run(user_input, confirmed=confirmed)
                artifact_ids = list(context.application_context.services.pop("recent_artifact_ids", []))
            finally:
                context.application_context.services.pop("run_context", None)
            payload = {
                "final_answer": result.final_answer,
                "execution_trace": [
                    {
                        "name": step.name,
                        "status": step.status,
                        "detail": step.detail,
                    }
                    for step in result.steps
                ],
                "observations": [
                    {
                        "kind": observation.kind,
                        "payload": dict(observation.payload),
                    }
                    for observation in result.observations
                ],
                "artifact_ids": artifact_ids,
            }
            if result.status == "requires_confirmation":
                payload["needs_approval"] = True
                payload["approval_reason"] = result.termination_reason or "action requires confirmation"
                payload["risk_class"] = "high"
            return payload

        self.register(
            CapabilitySpec(
                name=name,
                runner=_runner,
                source="application",
                description=f"Application capability: {name}",
                safety_level="application",
                risk_class="moderate",
                output_type="application_result",
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
                    cost_hint="medium",
                    latency_hint="medium",
                    risk_class="low",
                    dependency_readiness="partial" if spec.required_capabilities else "ready",
                    output_type="skill_result",
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
                    cost_hint=tool.cost_hint,
                    latency_hint=tool.latency_hint,
                    risk_class=tool.risk_class,
                    dependency_readiness=tool.dependency_readiness,
                    output_type=tool.output_type,
                )
            )

    def register_tool_registry(self, tools: Any) -> None:
        for tool_name in tools.names():
            tool = tools.get(tool_name)
            if tool is None:
                continue
            self.register_tool(tool)

    def register_tool(self, tool: ToolSpec) -> None:
        def _runner(user_input: str, context: Any, session: Any, _tool: ToolSpec = tool) -> dict[str, Any]:
            return {
                "final_answer": (
                    f"tool '{_tool.name}' is registered for discovery only; "
                    "use the Codex action loop to execute it"
                ),
                "execution_trace": [
                    {
                        "name": _tool.name,
                        "status": "blocked",
                        "detail": "tool discovery entry cannot execute inside capability loop",
                    }
                ],
                "observations": [
                    {
                        "kind": "tool_discovery",
                        "payload": {
                            "tool_name": _tool.name,
                            "permission_level": _tool.permission_level,
                        },
                    }
                ],
            }

        self.register(
            CapabilitySpec(
                name=f"tool:{tool.name}",
                runner=_runner,
                source="tool",
                description=tool.description,
                safety_level=tool.permission_level,
                input_contract=dict(tool.input_schema),
                cost_hint="medium",
                latency_hint="medium",
                risk_class=_risk_class_for_permission(tool.permission_level),
                dependency_readiness="ready",
                output_type="tool",
                execution_mode="codex_only",
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

    def executable_names(self) -> list[str]:
        return [
            name
            for name, spec in self._capabilities.items()
            if spec.execution_mode == "direct"
        ]

    def discovery_names(self) -> list[str]:
        return [
            name
            for name, spec in self._capabilities.items()
            if spec.execution_mode != "direct"
        ]
