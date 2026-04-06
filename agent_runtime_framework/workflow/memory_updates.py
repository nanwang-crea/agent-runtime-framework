from __future__ import annotations

from typing import Any

from agent_runtime_framework.workflow.models import AgentGraphState


def _ensure_memory_state_dicts(state: AgentGraphState) -> None:
    state.memory_state.clarification_memory = dict(state.memory_state.clarification_memory or {})
    state.memory_state.semantic_memory = dict(state.memory_state.semantic_memory or {})
    state.memory_state.execution_memory = dict(state.memory_state.execution_memory or {})
    state.memory_state.preference_memory = dict(state.memory_state.preference_memory or {})


def remember_semantic_plan(
    state: AgentGraphState,
    *,
    interpreted_target: dict[str, Any] | None = None,
    search_plan: dict[str, Any] | None = None,
    read_plan: dict[str, Any] | None = None,
) -> None:
    _ensure_memory_state_dicts(state)
    semantic = state.memory_state.semantic_memory
    if interpreted_target is not None:
        semantic["interpreted_target"] = dict(interpreted_target)
    if search_plan is not None:
        semantic["search_plan"] = dict(search_plan)
    if read_plan is not None:
        semantic["read_plan"] = dict(read_plan)


def remember_execution_feedback(
    state: AgentGraphState,
    *,
    ineffective_actions: list[str] | None = None,
    conflicts: list[str] | None = None,
    quality_summary: dict[str, Any] | None = None,
) -> None:
    _ensure_memory_state_dicts(state)
    execution = state.memory_state.execution_memory
    if ineffective_actions is not None:
        execution["ineffective_actions"] = [str(item) for item in ineffective_actions if str(item).strip()]
    if conflicts is not None:
        execution["conflicts"] = [str(item) for item in conflicts if str(item).strip()]
    if quality_summary is not None:
        execution["quality_summary"] = dict(quality_summary)


def remember_clarification(
    state: AgentGraphState,
    *,
    active_question: str | None = None,
    candidate_items: list[str] | None = None,
    last_resolution: dict[str, Any] | None = None,
) -> None:
    _ensure_memory_state_dicts(state)
    clarification = state.memory_state.clarification_memory
    if active_question is not None:
        clarification["active_question"] = str(active_question)
    if candidate_items is not None:
        clarification["candidate_items"] = [str(item) for item in candidate_items if str(item).strip()]
    if last_resolution is not None:
        clarification["last_resolution"] = dict(last_resolution)
