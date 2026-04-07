from agent_runtime_framework.errors import (
    AgentRuntimeError,
    AppError,
    PolicyViolationError,
    ToolExecutionError,
)


def test_framework_errors_are_typed():
    assert issubclass(PolicyViolationError, AgentRuntimeError)
    assert issubclass(ToolExecutionError, AgentRuntimeError)


def test_app_error_serializes_trace_id_and_context():
    error = AppError(
        code="INTERNAL_ERROR",
        message="boom",
        stage="run",
        context={"workspace": "/tmp/demo", "route": "codex"},
    )

    payload = error.as_dict()

    assert payload["trace_id"]
    assert payload["context"] == {"workspace": "/tmp/demo", "route": "codex"}
