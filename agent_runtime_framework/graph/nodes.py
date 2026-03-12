from __future__ import annotations

from typing import Callable, Generic, TypeVar

from agent_runtime_framework.graph.runtime import ExecutionContext


StateT = TypeVar("StateT")


class BaseNode(Generic[StateT]):
    def run(self, state: StateT, context: ExecutionContext) -> StateT:
        raise NotImplementedError


class FunctionNode(BaseNode[StateT]):
    def __init__(self, fn: Callable[[StateT, ExecutionContext], StateT]) -> None:
        self.fn = fn

    def run(self, state: StateT, context: ExecutionContext) -> StateT:
        return self.fn(state, context)
