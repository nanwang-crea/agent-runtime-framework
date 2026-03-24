from __future__ import annotations

from contextlib import contextmanager
from threading import Lock

from agent_runtime_framework.core.specs import ToolSpec
from agent_runtime_framework.tools.models import ToolCall, ToolResult

_SERIALIZATION_LOCKS: dict[str, Lock] = {}
_SERIALIZATION_GUARD = Lock()


def _resolve_tool_runtime(context):
    if context is None:
        return None
    services = getattr(context, "services", None)
    if isinstance(services, dict) and services.get("tool_runtime") is not None:
        return services["tool_runtime"]
    app_context = getattr(context, "application_context", None)
    app_services = getattr(app_context, "services", None)
    if isinstance(app_services, dict):
        return app_services.get("tool_runtime")
    return None


@contextmanager
def _serialized_execution(tool: ToolSpec, call: ToolCall):
    argument_name = tool.serialize_by_argument
    if not argument_name:
        yield
        return
    raw_value = call.arguments.get(argument_name)
    if raw_value is None:
        yield
        return
    key = f"{tool.name}:{raw_value}"
    with _SERIALIZATION_GUARD:
        lock = _SERIALIZATION_LOCKS.setdefault(key, Lock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def execute_tool_call(
    tool: ToolSpec,
    call: ToolCall,
    *,
    task,
    context,
) -> ToolResult:
    attempts = 0
    runtime = _resolve_tool_runtime(context)
    if runtime is not None and hasattr(runtime, "before_tool_call"):
        maybe_call = runtime.before_tool_call(tool, call, task)
        if isinstance(maybe_call, ToolCall):
            call = maybe_call
    while True:
        attempts += 1
        try:
            with _serialized_execution(tool, call):
                output = tool.executor(task, context, call.arguments)
            result = ToolResult(
                tool_name=tool.name,
                success=True,
                output=output,
                attempt_count=attempts,
            )
            if runtime is not None and hasattr(runtime, "after_tool_call"):
                maybe_result = runtime.after_tool_call(tool, call, result, task)
                if isinstance(maybe_result, ToolResult):
                    result = maybe_result
            return result
        except Exception as exc:
            if attempts > tool.max_retries:
                return ToolResult(
                    tool_name=tool.name,
                    success=False,
                    error=str(exc),
                    exception=exc,
                    attempt_count=attempts,
                )
