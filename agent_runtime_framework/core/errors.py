from __future__ import annotations

from dataclasses import dataclass


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
        }


class PolicyViolationError(AgentRuntimeError):
    """Raised when a policy blocks execution."""


class ToolExecutionError(AgentRuntimeError):
    """Raised when a tool execution fails."""
