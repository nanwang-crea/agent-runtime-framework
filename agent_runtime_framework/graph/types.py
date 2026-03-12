from __future__ import annotations

from typing import Callable, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from agent_runtime_framework.graph.runtime import ExecutionContext


StateT = TypeVar("StateT")


class RouteDecision(BaseModel):
    next_node: str
    reason: str
    source: str | None = None


@runtime_checkable
class Node(Protocol[StateT]):
    def run(self, state: StateT, context: ExecutionContext) -> StateT: ...


ResolverFunc = Callable[[StateT, ExecutionContext, list[str]], RouteDecision | str]
