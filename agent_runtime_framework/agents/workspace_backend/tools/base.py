from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent_runtime_framework.core.specs import ToolSpec

ToolHandler = Callable[[Any, Any, dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class WorkspaceToolDefinition:
    name: str
    description: str
    handler: ToolHandler
    input_schema: dict[str, Any] = field(default_factory=dict)
    permission_level: str = "read"
    prompt_snippet: str = ""
    prompt_guidelines: list[str] = field(default_factory=list)
    serialize_by_argument: str | None = None
    required_arguments: tuple[str, ...] = ()
    timeout_seconds: float = 10.0

    def to_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            executor=self.handler,
            input_schema=dict(self.input_schema),
            permission_level=self.permission_level,
            prompt_snippet=self.prompt_snippet,
            prompt_guidelines=list(self.prompt_guidelines),
            serialize_by_argument=self.serialize_by_argument,
            required_arguments=self.required_arguments,
            timeout_seconds=self.timeout_seconds,
        )
