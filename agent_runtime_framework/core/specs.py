from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class Planner(Protocol):
    def __call__(
        self,
        task: Any,
        context: Any,
        observations: list[Any],
    ) -> dict[str, Any]: ...


class Evaluator(Protocol):
    def __call__(
        self,
        task: Any,
        context: Any,
        step_result: Any,
    ) -> dict[str, Any]: ...


class Responder(Protocol):
    def __call__(
        self,
        task: Any,
        context: Any,
        observations: list[Any],
    ) -> str: ...


class MemoryAdapter(Protocol):
    def load(self, task: Any, context: Any) -> list[Any]: ...

    def commit(self, task: Any, context: Any, result: Any) -> None: ...


class Policy(Protocol):
    def check_input(self, task: Any, context: Any) -> None: ...


class ToolExecutor(Protocol):
    def __call__(
        self,
        task: Any,
        context: Any,
        arguments: dict[str, Any],
    ) -> Any: ...


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    executor: ToolExecutor
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    permission_level: str = "read"
    timeout_seconds: float = 10.0
    max_retries: int = 0
    idempotent: bool = True
    prompt_snippet: str = ""
    prompt_guidelines: list[str] = field(default_factory=list)
    serialize_by_argument: str | None = None


@dataclass(slots=True)
class AgentSpec:
    name: str
    description: str
    planner: Planner
    evaluator: Evaluator
    responder: Responder
    tools: list[ToolSpec] = field(default_factory=list)
    memory_adapter: MemoryAdapter | None = None
    policy: Policy | None = None
