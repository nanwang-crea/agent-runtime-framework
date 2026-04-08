from __future__ import annotations

from typing import Any

from agent_runtime_framework.workflow.state.models import AgentGraphState


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


def build_planner_memory_view(state: AgentGraphState) -> dict[str, Any]:
    semantic = dict(state.memory_state.semantic_memory or {})
    execution = dict(state.memory_state.execution_memory or {})
    iteration_lookup = {
        int(item.get("iteration") or 0): str(item.get("planner_summary") or "").strip()
        for item in state.iteration_summaries
        if isinstance(item, dict)
    }
    derived_ineffective_actions = _dedupe(
        [
            iteration_lookup.get(int(item.get("iteration") or 0), "")
            for item in state.failure_history[-2:]
            if isinstance(item, dict) and str(item.get("status") or "") != "accepted"
        ]
    )
    return {
        "confirmed_targets": [str(item) for item in semantic.get("confirmed_targets", []) or [] if str(item).strip()],
        "excluded_targets": [str(item) for item in semantic.get("excluded_targets", []) or [] if str(item).strip()],
        "open_issues": list(state.open_issues),
        "ineffective_actions": _dedupe([str(item) for item in execution.get("ineffective_actions", []) or [] if str(item).strip()]) or derived_ineffective_actions,
        "recent_failures": [dict(item) for item in state.failure_history[-2:] if isinstance(item, dict)],
        "recent_recovery": [dict(item) for item in state.recovery_history[-2:] if isinstance(item, dict)],
        "search_plan": dict(semantic.get("search_plan") or {}),
        "read_plan": dict(semantic.get("read_plan") or {}),
    }


def build_semantic_memory_view(state: AgentGraphState) -> dict[str, Any]:
    return {
        "original_goal": state.goal_envelope.goal,
        "clarification_memory": dict(state.memory_state.clarification_memory or {}),
        "semantic_memory": dict(state.memory_state.semantic_memory or {}),
        "execution_memory": dict(state.memory_state.execution_memory or {}),
    }


def build_judge_memory_view(state: AgentGraphState) -> dict[str, Any]:
    semantic = dict(state.memory_state.semantic_memory or {})
    execution = dict(state.memory_state.execution_memory or {})
    clarification = dict(state.memory_state.clarification_memory or {})
    return {
        "confirmed_targets": [str(item) for item in semantic.get("confirmed_targets", []) or [] if str(item).strip()],
        "excluded_targets": [str(item) for item in semantic.get("excluded_targets", []) or [] if str(item).strip()],
        "clarification_history": [dict(item) for item in clarification.get("clarification_history", []) or [] if isinstance(item, dict)],
        "last_resolution": dict(clarification.get("last_resolution") or {}),
        "quality_summary": dict(execution.get("quality_summary") or {}),
        "conflicts": [str(item) for item in execution.get("conflicts", []) or [] if str(item).strip()],
        "semantic_constraints": {
            "interpreted_target": dict(semantic.get("interpreted_target") or {}),
            "search_plan": dict(semantic.get("search_plan") or {}),
            "read_plan": dict(semantic.get("read_plan") or {}),
        },
    }


def build_response_memory_view_from_payload(memory_state: dict[str, Any] | None) -> dict[str, Any]:
    memory_state = dict(memory_state or {})
    semantic = dict(memory_state.get("semantic_memory") or {})
    clarification = dict(memory_state.get("clarification_memory") or {})
    return {
        "confirmed_targets": [str(item) for item in semantic.get("confirmed_targets", []) or [] if str(item).strip()],
        "excluded_targets": [str(item) for item in semantic.get("excluded_targets", []) or [] if str(item).strip()],
        "last_resolution": dict(clarification.get("last_resolution") or {}),
        "semantic_constraints": {
            "interpreted_target": dict(semantic.get("interpreted_target") or {}),
            "search_plan": dict(semantic.get("search_plan") or {}),
            "read_plan": dict(semantic.get("read_plan") or {}),
        },
    }
