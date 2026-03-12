from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from agent_runtime_framework.graph.checks import validate_graph
from agent_runtime_framework.graph.runtime import ExecutionContext, GraphResult
from agent_runtime_framework.graph.types import Node, RouteDecision


StateT = TypeVar("StateT")
END = "__end__"


@dataclass(slots=True)
class ConditionalRoute(Generic[StateT]):
    resolver: Any
    path_map: dict[str, str]


class GraphExecutor(Generic[StateT]):
    def __init__(
        self,
        nodes: dict[str, Node[StateT]],
        edges: dict[str, list[str]],
        conditional_edges: dict[str, ConditionalRoute[StateT]],
        entry_point: str,
        finish_point: str,
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.conditional_edges = conditional_edges
        self.entry_point = entry_point
        self.finish_point = finish_point

    def _run_node(self, node: Node[StateT], state: StateT, context: ExecutionContext) -> StateT:
        return node.run(state, context)

    def _resolve_next_node(
        self,
        current_node: str,
        state: StateT,
        context: ExecutionContext,
    ) -> str | None:
        if current_node in self.conditional_edges:
            route = self.conditional_edges[current_node]
            available_actions = list(route.path_map.keys())
            decision = route.resolver.decide(state, context, available_actions)
            if not isinstance(decision, RouteDecision):
                raise TypeError("条件路由器必须返回 RouteDecision。")
            if decision.next_node not in route.path_map:
                raise ValueError(f"非法下一节点: {decision.next_node}")
            if hasattr(state, "record_route"):
                source = decision.source or route.resolver.__class__.__name__
                state.record_route(source, decision.next_node, decision.reason)
            return route.path_map[decision.next_node]

        static_edges = self.edges.get(current_node, [])
        if not static_edges:
            return None
        return static_edges[0]

    def run(
        self,
        initial_state: StateT,
        context: ExecutionContext | None = None,
        *,
        max_steps: int | None = None,
    ) -> GraphResult[StateT]:
        context = context or ExecutionContext()
        state = initial_state
        if getattr(state, "current_node", None) is None:
            state.current_node = self.entry_point

        while not getattr(state, "done", False):
            if max_steps is not None and getattr(state, "step_count", 0) >= max_steps:
                state.done = True
                state.status = "max_steps_exceeded"
                state.termination_reason = "max_steps_reached"
                break

            current_node = state.current_node
            if current_node is None:
                raise ValueError("当前状态缺少 current_node。")

            node = self.nodes[current_node]
            state.step_count += 1
            state.last_node = current_node
            state.add_trace(current_node)
            state = self._run_node(node, state, context)

            if current_node == self.finish_point:
                state.done = True
                if getattr(state, "status", "running") == "running":
                    state.status = "completed"
                state.termination_reason = "finish_point_reached"
                break

            next_node = self._resolve_next_node(current_node, state, context)
            if next_node in {None, END}:
                state.done = True
                if getattr(state, "status", "running") == "running":
                    state.status = "completed"
                state.termination_reason = "graph_ended"
                break

            state.current_node = next_node

        return GraphResult(
            final_state=state,
            execution_trace=list(getattr(state, "execution_trace", [])),
            routing_history=list(getattr(state, "routing_history", [])),
            status=getattr(state, "status", "completed"),
            termination_reason=getattr(state, "termination_reason", None),
        )


class StateGraph(Generic[StateT]):
    def __init__(self) -> None:
        self.nodes: dict[str, Node[StateT]] = {}
        self.edges: dict[str, list[str]] = {}
        self.conditional_edges: dict[str, ConditionalRoute[StateT]] = {}
        self.entry_point: str | None = None
        self.finish_point: str | None = None

    def add_node(self, name: str, node: Node[StateT]) -> "StateGraph[StateT]":
        self.nodes[name] = node
        return self

    def add_edge(self, from_node: str, to_node: str) -> "StateGraph[StateT]":
        self.edges.setdefault(from_node, []).append(to_node)
        return self

    def add_conditional_edges(
        self,
        node_name: str,
        resolver: Any,
        destinations: list[str] | dict[str, str],
    ) -> "StateGraph[StateT]":
        path_map = (
            destinations
            if isinstance(destinations, dict)
            else {destination: destination for destination in destinations}
        )
        self.conditional_edges[node_name] = ConditionalRoute(
            resolver=resolver,
            path_map=path_map,
        )
        return self

    def set_entry_point(self, name: str) -> "StateGraph[StateT]":
        self.entry_point = name
        return self

    def set_finish_point(self, name: str) -> "StateGraph[StateT]":
        self.finish_point = name
        return self

    def compile(self) -> GraphExecutor[StateT]:
        validate_graph(
            nodes=set(self.nodes.keys()),
            edges=self.edges,
            conditional_edges={
                name: list(route.path_map.values())
                for name, route in self.conditional_edges.items()
            },
            entry_point=self.entry_point,
            finish_point=self.finish_point,
        )
        return GraphExecutor(
            nodes=self.nodes,
            edges=self.edges,
            conditional_edges=self.conditional_edges,
            entry_point=self.entry_point or "",
            finish_point=self.finish_point or "",
        )
