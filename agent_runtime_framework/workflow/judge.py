from __future__ import annotations

from typing import Any

from agent_runtime_framework.workflow.models import AgentGraphState, GoalEnvelope, JudgeDecision, normalize_aggregated_workflow_payload


def _needs_verification(goal_envelope: GoalEnvelope) -> bool:
    intent = goal_envelope.intent
    return "verify" in intent or "change" in intent or bool(goal_envelope.constraints.get("requires_verification"))


def judge_progress(
    goal_envelope: GoalEnvelope,
    aggregated_payload: dict[str, Any] | None,
    graph_state: AgentGraphState,
) -> JudgeDecision:
    payload = normalize_aggregated_workflow_payload(aggregated_payload)
    max_iterations = int(goal_envelope.constraints.get("max_iterations") or 0)
    if max_iterations and graph_state.current_iteration >= max_iterations:
        return JudgeDecision(
            status="stop_due_to_cost",
            reason="Iteration budget exhausted",
            missing_evidence=["additional iterations"],
            coverage_report={"iterations_used": graph_state.current_iteration, "max_iterations": max_iterations},
        )

    open_questions = [str(item) for item in payload.get("open_questions", []) or [] if str(item).strip()]
    if open_questions:
        return JudgeDecision(
            status="needs_clarification",
            reason=open_questions[-1],
            missing_evidence=open_questions,
            coverage_report={"open_questions": open_questions},
        )

    evidence_count = len(payload.get("evidence_items", []) or []) + len(payload.get("chunks", []) or []) + len(payload.get("facts", []) or [])
    if evidence_count <= 0 and not (payload.get("summaries") or []):
        return JudgeDecision(
            status="needs_more_evidence",
            reason="Not enough evidence collected yet",
            missing_evidence=["grounded evidence"],
            coverage_report={"evidence_count": evidence_count},
            replan_hint={
                "goal_gap": "grounded_evidence_missing",
                "recommended_next_actions": ["content_search", "chunked_file_read"],
                "must_include": ["grounded evidence"],
                "must_avoid": ["final_response"],
            },
        )

    verification = payload.get("verification")
    verification_ok = isinstance(verification, dict) and bool(verification.get("success", verification.get("status") == "passed"))
    if _needs_verification(goal_envelope) and not verification_ok:
        return JudgeDecision(
            status="needs_verification",
            reason="Verification coverage is missing",
            missing_evidence=["verification"],
            coverage_report={"verification": verification or {"status": "missing"}},
            replan_hint={
                "goal_gap": "verification_missing",
                "next_node_type": "verification",
                "verification_type": "post_change",
                "recommended_next_actions": ["verification"],
                "must_include": ["verification"],
                "must_avoid": ["repeat_same_write_without_verification"],
            },
        )

    return JudgeDecision(
        status="accepted",
        reason="Collected sufficient evidence",
        coverage_report={"evidence_count": evidence_count, "verification": verification or {}},
    )
