import pytest

from agent_runtime_framework.core.specs import AgentSpec, ToolSpec
from agent_runtime_framework.tools.executor import execute_tool_call
from agent_runtime_framework.tools.models import ToolCall
from agent_runtime_framework.tools.registry import ToolRegistry


def _echo_tool(task, context, arguments):
    return {"echo": arguments["value"]}


def _failing_once_tool_factory():
    state = {"attempts": 0}

    def _tool(task, context, arguments):
        state["attempts"] += 1
        if state["attempts"] == 1:
            raise RuntimeError("fail once")
        return {"ok": True}

    return _tool


def _planner(task, context, observations):
    return {"kind": "finish", "answer": "done"}


def _evaluator(task, context, step_result):
    return {"status": "completed"}


def _responder(task, context, observations):
    return "done"


def test_registry_rejects_duplicate_tools():
    tool = ToolSpec(
        name="echo",
        description="echo input",
        executor=_echo_tool,
    )
    registry = ToolRegistry([tool])

    with pytest.raises(ValueError):
        registry.register(tool)


def test_execute_tool_call_retries_until_success():
    tool = ToolSpec(
        name="retrying",
        description="retry once",
        executor=_failing_once_tool_factory(),
        max_retries=1,
    )

    result = execute_tool_call(
        tool,
        ToolCall(tool_name="retrying", arguments={"value": "x"}),
        task=None,
        context=None,
    )

    assert result.success is True
    assert result.attempt_count == 2


def test_agent_spec_allows_minimal_callable_components():
    spec = AgentSpec(
        name="demo",
        description="demo agent",
        planner=_planner,
        evaluator=_evaluator,
        responder=_responder,
    )

    assert spec.name == "demo"
    assert spec.tools == []
