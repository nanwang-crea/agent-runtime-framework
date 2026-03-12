import pytest

from agent_runtime_framework.graph import (
    BaseState,
    BaseNode,
    FallbackRouter,
    FunctionNode,
    GraphValidationError,
    RuleRouter,
    StateGraph,
)


class DemoState(BaseState):
    value: int = 0
    result: str = ""


class IncrementNode:
    def run(self, state: DemoState, context) -> DemoState:
        state.value += 1
        return state


class FinishNode:
    def run(self, state: DemoState, context) -> DemoState:
        state.result = f"done:{state.value}"
        return state


def route_next(state: DemoState, context, available_actions: list[str]) -> str:
    return "finish" if state.value >= 2 else "increment"


def test_base_state_records_trace_and_notes():
    state = DemoState()

    state.add_trace("prepare")
    state.add_note("note")
    state.record_route("rule", "finish", "done")

    assert state.execution_trace == ["prepare"]
    assert state.notes == ["note"]
    assert state.routing_history == [
        {
            "source": "rule",
            "next_node": "finish",
            "reason": "done",
        }
    ]


def test_state_graph_runs_simple_conditional_loop():
    graph = StateGraph[DemoState]()
    graph.add_node("increment", IncrementNode())
    graph.add_node("finish", FinishNode())
    graph.add_conditional_edges(
        "increment",
        RuleRouter(route_next),
        {
            "increment": "increment",
            "finish": "finish",
        },
    )
    graph.set_entry_point("increment")
    graph.set_finish_point("finish")

    result = graph.compile().run(DemoState())

    assert result.status == "completed"
    assert result.final_state.value == 2
    assert result.final_state.result == "done:2"
    assert result.execution_trace == ["increment", "increment", "finish"]


def test_fallback_router_uses_fallback_after_primary_failure():
    class BrokenRouter:
        def decide(self, state, context, available_actions):
            raise RuntimeError("broken")

    router = FallbackRouter(BrokenRouter(), RuleRouter(route_next))
    state = DemoState(value=2)

    decision = router.decide(state, None, ["increment", "finish"])

    assert decision.next_node == "finish"
    assert decision.source == "fallback_rule"
    assert state.notes


def test_graph_validation_rejects_missing_entry_point():
    graph = StateGraph[DemoState]()
    graph.add_node("increment", IncrementNode())
    graph.add_node("finish", FinishNode())
    graph.add_edge("increment", "finish")
    graph.set_finish_point("finish")

    with pytest.raises(GraphValidationError):
        graph.compile()


def test_function_node_wraps_plain_callable():
    def finish_now(state: DemoState, context) -> DemoState:
        state.result = "wrapped"
        return state

    graph = StateGraph[DemoState]()
    graph.add_node("finish", FunctionNode(finish_now))
    graph.set_entry_point("finish")
    graph.set_finish_point("finish")

    result = graph.compile().run(DemoState())

    assert result.final_state.result == "wrapped"


def test_base_node_can_be_subclassed_directly():
    class FinishNodeImpl(BaseNode[DemoState]):
        def run(self, state: DemoState, context) -> DemoState:
            state.result = "base-node"
            return state

    graph = StateGraph[DemoState]()
    graph.add_node("finish", FinishNodeImpl())
    graph.set_entry_point("finish")
    graph.set_finish_point("finish")

    result = graph.compile().run(DemoState())

    assert result.final_state.result == "base-node"


def test_graph_executor_stops_when_max_steps_is_reached():
    graph = StateGraph[DemoState]()
    graph.add_node("increment", IncrementNode())
    graph.add_node("finish", FinishNode())
    graph.add_conditional_edges(
        "increment",
        RuleRouter(lambda state, context, actions: "increment"),
        {"increment": "increment"},
    )
    graph.set_entry_point("increment")
    graph.set_finish_point("finish")

    result = graph.compile().run(DemoState(), max_steps=1)

    assert result.final_state.done is True
    assert result.termination_reason == "max_steps_reached"
    assert result.status == "max_steps_exceeded"


def test_routing_history_is_structured():
    graph = StateGraph[DemoState]()
    graph.add_node("increment", IncrementNode())
    graph.add_node("finish", FinishNode())
    graph.add_conditional_edges(
        "increment",
        RuleRouter(route_next),
        {
            "increment": "increment",
            "finish": "finish",
        },
    )
    graph.set_entry_point("increment")
    graph.set_finish_point("finish")

    result = graph.compile().run(DemoState())

    assert result.routing_history
    first_route = result.routing_history[0]
    assert first_route["source"] == "rule"
    assert first_route["next_node"] in {"increment", "finish"}
    assert "reason" in first_route
