from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


class AgentRuntimeError(RuntimeError):
    """Base framework runtime error."""


@dataclass(slots=True)
class AppError(AgentRuntimeError):
    code: str
    message: str
    detail: str | None = None
    stage: str | None = None
    retriable: bool = False
    suggestion: str | None = None
    trace_id: str = field(default_factory=lambda: uuid4().hex[:12])
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        AgentRuntimeError.__init__(self, self.message)

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "stage": self.stage,
            "retriable": self.retriable,
            "suggestion": self.suggestion,
            "trace_id": self.trace_id,
            "context": dict(self.context),
        }


def normalize_app_error(
    exc: Exception,
    *,
    code: str = "INTERNAL_ERROR",
    message: str = "处理请求时发生了未预期错误。",
    detail: str | None = None,
    stage: str | None = None,
    retriable: bool = False,
    suggestion: str | None = None,
    context: dict[str, Any] | None = None,
) -> AppError:
    merged_context = _normalize_error_context(context)
    if isinstance(exc, AppError):
        return AppError(
            code=exc.code,
            message=exc.message,
            detail=exc.detail,
            stage=exc.stage,
            retriable=exc.retriable,
            suggestion=exc.suggestion,
            trace_id=exc.trace_id,
            context={**merged_context, **dict(exc.context)},
        )
    return AppError(
        code=code,
        message=message,
        detail=detail or f"{type(exc).__name__}: {exc}",
        stage=stage,
        retriable=retriable,
        suggestion=suggestion,
        context=merged_context,
    )


def log_app_error(
    logger: logging.Logger,
    error: AppError,
    *,
    exc: Exception | None = None,
    event: str = "app_error",
) -> None:
    payload = (
        f"{event} trace_id={error.trace_id} code={error.code} stage={error.stage or ''} "
        f"detail={error.detail or ''} context={dict(error.context)}"
    )
    if exc is not None and not isinstance(exc, AppError):
        logger.exception(payload)
        return
    logger.error(payload)


def _normalize_error_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {}
    normalized: dict[str, Any] = {}
    for key, value in context.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            normalized[str(key)] = value
            continue
        if isinstance(value, dict):
            normalized[str(key)] = {str(item_key): str(item_value) for item_key, item_value in value.items()}
            continue
        if isinstance(value, (list, tuple, set)):
            normalized[str(key)] = [str(item) for item in value]
            continue
        normalized[str(key)] = str(value)
    return normalized


class PolicyViolationError(AgentRuntimeError):
    """Raised when a policy blocks execution."""


class ToolExecutionError(AgentRuntimeError):
    """Raised when a tool execution fails."""
