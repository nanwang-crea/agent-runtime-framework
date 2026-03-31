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


def append_evidence_item(task: object, item: EvidenceItem) -> None:
    state = getattr(task, "state", None)
    if state is None:
        return
    state.evidence_items.append(item)
    state.confidence_state.evidence_confidence = min(1.0, state.confidence_state.evidence_confidence + max(item.relevance, 0.1) * 0.2)
