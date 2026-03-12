from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar


StateT = TypeVar("StateT")


@dataclass(slots=True)
class ExecutionContext:
    config: Any = None
    llm_client: Any = None
    services: dict[str, Any] = field(default_factory=dict)
    debug: bool = False


@dataclass(slots=True)
class GraphResult(Generic[StateT]):
    final_state: StateT
    execution_trace: list[str]
    routing_history: list[dict[str, str]]
    status: str
    termination_reason: str | None
