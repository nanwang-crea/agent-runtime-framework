from __future__ import annotations

from agent_runtime_framework.agents.codex.models import ConfidenceState, EvidenceItem, TaskIntent, TaskState


def build_initial_task_state(intent: TaskIntent) -> TaskState:
    return TaskState(
        task_intent=intent,
        resolved_target=intent.target_ref or intent.target_hint,
        resource_semantics={},
        evidence_items=[],
        known_facts=[],
        open_questions=["answer user goal"] if intent.needs_grounding else [],
        pending_actions=[],
        plan_state={},
        confidence_state=ConfidenceState(
            intent_confidence=float(intent.confidence or 0.0),
            target_confidence=float(intent.target_confidence or 0.0),
            evidence_confidence=0.0,
            answer_confidence=0.0,
        ),
        answer_mode=intent.expected_output or "direct_answer",
    )


def sync_task_state_from_memory(task: object) -> None:
    state = getattr(task, "state", None)
    memory = getattr(task, "memory", None)
    if state is None or memory is None:
        return
    state.known_facts = list(getattr(memory, "known_facts", []) or [])
    state.open_questions = list(getattr(memory, "open_questions", []) or [])
    state.pending_actions = list(getattr(state, "pending_actions", []) or [])
    if getattr(task, "plan", None) is not None:
        plan = getattr(task, "plan")
        state.plan_state = {
            "status": getattr(plan, "status", ""),
            "tasks": [f"{item.title}:{item.kind}:{item.status}" for item in getattr(plan, "tasks", [])],
        }
        state.pending_actions = [
            getattr(item, "kind", "")
            for item in getattr(plan, "tasks", [])
            if getattr(item, "status", "") != "completed"
        ]
    if getattr(plan := getattr(task, "plan", None), "target_semantics", None) is not None:
        semantics = getattr(plan, "target_semantics")
        state.resource_semantics = {
            "path": getattr(semantics, "path", ""),
            "resource_kind": getattr(semantics, "resource_kind", ""),
            "is_container": bool(getattr(semantics, "is_container", False)),
            "allowed_actions": list(getattr(semantics, "allowed_actions", []) or []),
        }
        if state.resource_semantics.get("path"):
            state.resolved_target = state.resource_semantics.get("path", "")


def append_evidence_item(task: object, item: EvidenceItem) -> None:
    state = getattr(task, "state", None)
    if state is None:
        return
    state.evidence_items.append(item)
    state.confidence_state.evidence_confidence = min(1.0, state.confidence_state.evidence_confidence + max(item.relevance, 0.1) * 0.2)
