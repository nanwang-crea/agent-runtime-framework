from __future__ import annotations


class AgentRuntimeError(RuntimeError):
    """Base framework runtime error."""


class PolicyViolationError(AgentRuntimeError):
    """Raised when a policy blocks execution."""


class ToolExecutionError(AgentRuntimeError):
    """Raised when a tool execution fails."""
