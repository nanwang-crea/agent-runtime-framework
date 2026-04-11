from __future__ import annotations

from typing import Any


def _agent_graph_state_from_context(context: Any) -> Any | None:
    if context is None:
        return None
    if isinstance(context, dict):
        return context.get("agent_graph_state")
    return getattr(context, "agent_graph_state", None)


def require_agent_graph_state(context: Any) -> Any:
    state = _agent_graph_state_from_context(context)
    if state is None:
        raise RuntimeError("Missing agent_graph_state on workflow runtime context")
    return state


def optional_agent_graph_state(context: Any) -> Any | None:
    return _agent_graph_state_from_context(context)


def process_sink_from_context(context: Any) -> Any:
    if context is None:
        return None
    if isinstance(context, dict):
        return context.get("process_sink")
    return getattr(context, "process_sink", None)
