from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Protocol

from agent_runtime_framework.mcp.registry import McpRegistry
from agent_runtime_framework.skills.models import SkillResult
from agent_runtime_framework.tools import ToolCall, ToolRegistry, execute_tool_call


class SkillProvider(Protocol):
    def supports(self, skill_name: str) -> bool: ...

    def invoke(self, skill_name: str, input: dict[str, Any], context: Any) -> SkillResult: ...


@dataclass(slots=True)
class SkillRuntime:
    providers: list[SkillProvider] = field(default_factory=list)

    def register_provider(self, provider: SkillProvider) -> None:
        self.providers.append(provider)

    def invoke(self, skill_name: str, input: dict[str, Any], context: Any) -> SkillResult:
        normalized_name = str(skill_name or "").strip()
        if not normalized_name:
            raise ValueError("skill_name is required")
        payload = dict(input or {})
        for provider in self.providers:
            if provider.supports(normalized_name):
                return provider.invoke(normalized_name, payload, context)
        raise KeyError(f"unsupported skill: {normalized_name}")


@dataclass(slots=True)
class ToolSkillProvider:
    tool_registry: ToolRegistry

    def supports(self, skill_name: str) -> bool:
        return self.tool_registry.get(str(skill_name).strip()) is not None

    def invoke(self, skill_name: str, input: dict[str, Any], context: Any) -> SkillResult:
        tool_name = str(skill_name).strip()
        tool = self.tool_registry.require(tool_name)
        result = execute_tool_call(
            tool,
            ToolCall(tool_name=tool_name, arguments=dict(input.get("arguments") or {})),
            task=input.get("task") or SimpleNamespace(task_id=tool_name, goal=str(input.get("goal") or tool_name), state=SimpleNamespace()),
            context=context,
        )
        output = dict(result.output or {})
        summary = str(output.get("summary") or output.get("text") or output.get("content") or "").strip()
        return SkillResult(
            name=tool_name,
            success=bool(result.success),
            summary=summary or ("skill completed" if result.success else str(result.error or "skill failed")),
            payload=output,
            changed_paths=[str(item) for item in output.get("changed_paths", []) or [] if str(item).strip()],
            references=[
                str(item)
                for item in [
                    output.get("path"),
                    output.get("resolved_path"),
                    *list(output.get("references", []) or []),
                ]
                if str(item or "").strip()
            ],
            memory_hint=dict(output.get("memory_hint") or {}) or None,
        )


@dataclass(slots=True)
class McpSkillProvider:
    registry: McpRegistry
    invoker: Callable[[str, dict[str, Any], Any], dict[str, Any]]
    prefix: str = "mcp:"

    def supports(self, skill_name: str) -> bool:
        if not str(skill_name).strip().startswith(self.prefix):
            return False
        capability_name = str(skill_name).strip()[len(self.prefix):]
        if "/" not in capability_name:
            return False
        server_id, capability_id = capability_name.split("/", 1)
        return self.registry.get_capability(server_id, capability_id) is not None

    def invoke(self, skill_name: str, input: dict[str, Any], context: Any) -> SkillResult:
        normalized_name = str(skill_name).strip()
        output = dict(self.invoker(normalized_name, dict(input or {}), context) or {})
        summary = str(output.get("summary") or output.get("text") or "").strip()
        return SkillResult(
            name=normalized_name,
            success=bool(output.get("success", True)),
            summary=summary or "mcp skill completed",
            payload=output,
            changed_paths=[str(item) for item in output.get("changed_paths", []) or [] if str(item).strip()],
            references=[str(item) for item in output.get("references", []) or [] if str(item).strip()],
            memory_hint=dict(output.get("memory_hint") or {}) or None,
        )


__all__ = [
    "McpSkillProvider",
    "SkillProvider",
    "SkillRuntime",
    "ToolSkillProvider",
]
