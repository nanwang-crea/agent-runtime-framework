from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Any

from agent_runtime_framework.tools.specs import ToolSpec
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
    call = ToolCall(tool_name=tool.name, arguments=_repair_argument_aliases(tool, call.arguments))
    validation_error = _validate_arguments(tool, call.arguments)
    if validation_error is not None:
        return ToolResult(
            tool_name=tool.name,
            success=False,
            error=str(validation_error["message"]),
            attempt_count=0,
            metadata={"error": validation_error},
        )
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
                    metadata={
                        "error": {
                            "code": "TOOL_EXECUTION_ERROR",
                            "message": str(exc),
                            "retriable": attempts <= tool.max_retries,
                        }
                    },
                )


def _repair_argument_aliases(tool: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
    repaired = dict(arguments or {})
    for canonical, aliases in dict(getattr(tool, "argument_aliases", {}) or {}).items():
        if canonical in repaired:
            continue
        for alias in aliases:
            if alias in repaired:
                repaired[canonical] = repaired.pop(alias)
                break
    return repaired


def _validate_arguments(tool: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any] | None:
    for name in getattr(tool, "required_arguments", ()) or ():
        value = arguments.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            return {
                "code": "TOOL_VALIDATION_ERROR",
                "message": f"missing required argument: {name}",
                "field": name,
                "retriable": True,
            }
    for field, expected in dict(getattr(tool, "input_schema", {}) or {}).items():
        if field not in arguments:
            continue
        value = arguments[field]
        ok, normalized = _coerce_value(value, str(expected or ""))
        if not ok:
            return {
                "code": "TOOL_VALIDATION_ERROR",
                "message": f"invalid argument type for {field}: expected {expected}",
                "field": field,
                "expected_type": str(expected or ""),
                "actual_type": type(value).__name__,
                "retriable": True,
            }
        arguments[field] = normalized
    return None


def _coerce_value(value: Any, expected_type: str) -> tuple[bool, Any]:
    normalized = expected_type.strip().lower()
    if not normalized:
        return True, value
    if normalized == "string":
        return isinstance(value, str), value
    if normalized == "integer":
        if isinstance(value, bool):
            return False, value
        if isinstance(value, int):
            return True, value
        if isinstance(value, str) and value.strip().isdigit():
            return True, int(value.strip())
        return False, value
    if normalized == "boolean":
        if isinstance(value, bool):
            return True, value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "false"}:
                return True, lowered == "true"
        return False, value
    return True, value
