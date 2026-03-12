import pytest

from agent_runtime_framework.core.errors import (
    AgentRuntimeError,
    PolicyViolationError,
    ToolExecutionError,
)
from agent_runtime_framework.core.models import RunResult, RuntimeContext, RuntimeLimits, Task


def test_runtime_limits_defaults():
    limits = RuntimeLimits()

    assert limits.max_steps > 0
    assert limits.max_tool_calls > 0


def test_run_result_holds_structured_output():
    result = RunResult(status="completed", final_answer="ok")

    assert result.status == "completed"
    assert result.final_answer == "ok"


def test_task_and_runtime_context_minimal_creation():
    task = Task(user_input="hello")
    context = RuntimeContext()

    assert task.user_input == "hello"
    assert context.services == {}


def test_framework_errors_are_typed():
    assert issubclass(PolicyViolationError, AgentRuntimeError)
    assert issubclass(ToolExecutionError, AgentRuntimeError)
