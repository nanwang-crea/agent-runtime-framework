from __future__ import annotations

from typing import Any

from agent_runtime_framework.memory import TaskSnapshot, trim_task_snapshot
from agent_runtime_framework.workflow.state.models import AgentGraphState, SessionMemoryState, WorkingMemory


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _ineffective_actions(state: AgentGraphState) -> list[str]:
    iteration_lookup = {
        int(item.get("iteration") or 0): str(item.get("planner_summary") or "").strip()
        for item in state.iteration_summaries
        if isinstance(item, dict)
    }
    return _dedupe(
        [
            iteration_lookup.get(int(item.get("iteration") or 0), "")
            for item in state.failure_history[-2:]
            if isinstance(item, dict) and str(item.get("status") or "") != "accepted"
        ]
    )


def build_task_snapshot_view(state: AgentGraphState) -> dict[str, Any]:
    session_memory = state.memory_state.session_memory
    snapshot = trim_task_snapshot(
        TaskSnapshot(
            goal=state.goal_envelope.goal,
            recent_focus=[
                item
                for item in [session_memory.last_active_target, *list(session_memory.last_read_files)]
                if str(item or "").strip()
            ],
            recent_paths=list(session_memory.recent_paths),
            last_action_summary=session_memory.last_action_summary,
            last_clarification=dict(session_memory.last_clarification)
            if isinstance(session_memory.last_clarification, dict)
            else None,
            long_term_hints=dict(state.memory_state.long_term_memory or {}),
        )
    )
    return {
        "goal": snapshot.goal,
        "recent_focus": list(snapshot.recent_focus),
        "recent_paths": list(snapshot.recent_paths),
        "last_action_summary": snapshot.last_action_summary,
        "last_clarification": dict(snapshot.last_clarification) if isinstance(snapshot.last_clarification, dict) else None,
        "long_term_hints": dict(snapshot.long_term_hints),
    }


def build_working_memory_view(state: AgentGraphState) -> dict[str, Any]:
    working_memory: WorkingMemory = state.memory_state.working_memory
    return {
        "active_target": working_memory.active_target,
        "confirmed_targets": list(working_memory.confirmed_targets),
        "excluded_targets": list(working_memory.excluded_targets),
        "current_step": working_memory.current_step,
        "open_issues": list(working_memory.open_issues or state.open_issues),
        "last_tool_result_summary": dict(working_memory.last_tool_result_summary)
        if isinstance(working_memory.last_tool_result_summary, dict)
        else None,
        "ineffective_actions": _ineffective_actions(state),
        "recent_failures": [dict(item) for item in state.failure_history[-2:] if isinstance(item, dict)],
        "recent_recovery": [dict(item) for item in state.recovery_history[-2:] if isinstance(item, dict)],
    }


def build_response_context_view(memory_state: dict[str, Any] | None) -> dict[str, Any]:
    memory_state = dict(memory_state or {})
    session_memory = SessionMemoryState(**dict(memory_state.get("session_memory") or {}))
    working_memory = WorkingMemory(**dict(memory_state.get("working_memory") or {}))
    return {
        "recent_focus": [
            item
            for item in [session_memory.last_active_target, *list(session_memory.last_read_files)]
            if str(item or "").strip()
        ],
        "recent_paths": list(session_memory.recent_paths),
        "last_action_summary": session_memory.last_action_summary,
        "last_clarification": dict(session_memory.last_clarification)
        if isinstance(session_memory.last_clarification, dict)
        else None,
        "active_target": working_memory.active_target,
        "confirmed_targets": list(working_memory.confirmed_targets),
        "excluded_targets": list(working_memory.excluded_targets),
    }
__all__ = [
    "build_task_snapshot_view",
    "build_working_memory_view",
    "build_response_context_view",
]
