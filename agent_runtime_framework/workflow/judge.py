from __future__ import annotations

from typing import Any

from agent_runtime_framework.workflow.memory_views import build_judge_memory_view
from agent_runtime_framework.workflow.models import AgentGraphState, GoalEnvelope, JudgeDecision, normalize_aggregated_workflow_payload


def _needs_verification(goal_envelope: GoalEnvelope) -> bool:
    intent = goal_envelope.intent
    return "verify" in intent or "change" in intent or bool(goal_envelope.constraints.get("requires_verification"))


def _verification_status(payload: dict[str, Any], goal_envelope: GoalEnvelope) -> str:
    verification = payload.get("verification")
    if isinstance(verification, dict) and bool(verification.get("success", verification.get("status") == "passed")):
        return "satisfied"
    if _needs_verification(goal_envelope):
        return "missing"
    return "not_required"


def _quality_signals(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (payload.get("quality_signals", []) or []) if isinstance(item, dict)]


def _normalize_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def _path_matches(actual: str, expected: str) -> bool:
    actual_norm = _normalize_path(actual)
    expected_norm = _normalize_path(expected)
    if not actual_norm or not expected_norm:
        return False
    if actual_norm == expected_norm:
        return True
    if expected_norm.startswith("/"):
        return False
    if actual_norm.startswith("/"):
        return actual_norm.endswith(f"/{expected_norm}")
    return False


def _semantic_path_consistency_conflicts(payload: dict[str, Any], judge_memory_view: dict[str, Any]) -> list[str]:
    evidence_paths = [
        _normalize_path(item.get("relative_path") or item.get("path"))
        for item in payload.get("evidence_items", []) or []
        if isinstance(item, dict)
    ]
    read_paths = [
        _normalize_path(item.get("path") or item.get("relative_path"))
        for item in payload.get("chunks", []) or []
        if isinstance(item, dict)
    ]
    observed_paths = [path for path in [*evidence_paths, *read_paths] if path]
    conflicts: list[str] = []
    confirmed_targets = [_normalize_path(item) for item in judge_memory_view.get("confirmed_targets", []) or [] if _normalize_path(item)]
    excluded_targets = [_normalize_path(item) for item in judge_memory_view.get("excluded_targets", []) or [] if _normalize_path(item)]
    read_plan = dict((judge_memory_view.get("semantic_constraints") or {}).get("read_plan") or {})
    planned_read_path = _normalize_path(read_plan.get("target_path"))

    if confirmed_targets and observed_paths:
        for path in observed_paths:
            if not any(_path_matches(path, target) for target in confirmed_targets):
                conflicts.append(f"path_mismatch:{path}")
    if excluded_targets:
        for path in observed_paths:
            if any(_path_matches(path, target) for target in excluded_targets):
                conflicts.append(f"excluded_target:{path}")
    if planned_read_path:
        for path in read_paths:
            if not _path_matches(path, planned_read_path):
                conflicts.append(f"read_plan_mismatch:{path}")
    return conflicts


def _has_grounded_progress(signals: list[dict[str, Any]]) -> bool:
    grounded_contributions = {
        "grounded_evidence_collected",
        "workspace_context_collected",
        "verification_completed",
        "workspace_updated",
        "target_resolved",
        "tool_result_collected",
    }
    return any(str(item.get("progress_contribution") or "").strip() in grounded_contributions for item in signals)


def judge_progress(
    goal_envelope: GoalEnvelope,
    aggregated_payload: dict[str, Any] | None,
    graph_state: AgentGraphState,
) -> JudgeDecision:
    payload = normalize_aggregated_workflow_payload(aggregated_payload)
    judge_memory_view = build_judge_memory_view(graph_state)
    max_iterations = int(goal_envelope.constraints.get("max_iterations") or 0)
    if max_iterations and graph_state.current_iteration >= max_iterations:
        return JudgeDecision(
            status="stop_due_to_cost",
            reason="Iteration budget exhausted",
            missing_evidence=["additional iterations"],
            coverage_report={"iterations_used": graph_state.current_iteration, "max_iterations": max_iterations},
            diagnosis={
                "primary_gap": "iteration_budget_exhausted",
                "goal_status": "budget_exhausted",
                "verification_status": _verification_status(payload, goal_envelope),
            },
            strategy_guidance={
                "recommended_strategy": "summarize_current_progress",
                "focus": ["report_remaining_gaps"],
            },
        )

    open_questions = [str(item) for item in payload.get("open_questions", []) or [] if str(item).strip()]
    if open_questions:
        return JudgeDecision(
            status="needs_clarification",
            reason=open_questions[-1],
            missing_evidence=open_questions,
            coverage_report={"open_questions": open_questions},
            diagnosis={
                "primary_gap": "clarification_missing",
                "goal_status": "ambiguous",
                "verification_status": _verification_status(payload, goal_envelope),
            },
            strategy_guidance={
                "recommended_strategy": "request_target_clarification",
                "focus": ["clarify_ambiguous_target"],
            },
        )

    conflicts = [str(item) for item in payload.get("conflicts", []) or [] if str(item).strip()]
    conflicts.extend(_semantic_path_consistency_conflicts(payload, judge_memory_view))
    quality_signals = _quality_signals(payload)
    if conflicts:
        return JudgeDecision(
            status="needs_more_evidence",
            reason="Conflicting evidence needs to be resolved before answering",
            missing_evidence=["resolved conflict"],
            coverage_report={"conflicts": conflicts, "quality_signals": quality_signals},
            replan_hint={
                "goal_gap": "conflicting_evidence",
                "recommended_next_actions": ["target_resolution", "chunked_file_read"],
                "must_include": ["conflict resolution"],
                "must_avoid": ["final_response"],
            },
            diagnosis={
                "primary_gap": "conflicting_evidence",
                "goal_status": "conflicted",
                "verification_status": _verification_status(payload, goal_envelope),
            },
            strategy_guidance={
                "recommended_strategy": "resolve_conflict_before_answering",
                "focus": ["target_resolution", "compare evidence"],
                "avoid": ["final_response"],
            },
        )

    evidence_count = len(payload.get("evidence_items", []) or []) + len(payload.get("chunks", []) or []) + len(payload.get("facts", []) or [])
    summaries = payload.get("summaries") or []
    if (evidence_count <= 0 and not summaries) or (evidence_count <= 0 and summaries and not _has_grounded_progress(quality_signals)):
        return JudgeDecision(
            status="needs_more_evidence",
            reason="Not enough evidence collected yet",
            missing_evidence=["grounded evidence"],
            coverage_report={"evidence_count": evidence_count, "quality_signals": quality_signals},
            replan_hint={
                "goal_gap": "grounded_evidence_missing",
                "recommended_next_actions": ["content_search", "chunked_file_read"],
                "must_include": ["grounded evidence"],
                "must_avoid": ["final_response"],
            },
            diagnosis={
                "primary_gap": "grounded_evidence_missing",
                "goal_status": "insufficient_coverage",
                "evidence_status": "missing",
                "verification_status": _verification_status(payload, goal_envelope),
            },
            strategy_guidance={
                "recommended_strategy": "gather_grounded_evidence",
                "focus": ["content_search", "chunked_file_read"],
                "avoid": ["final_response"],
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
            diagnosis={
                "primary_gap": "verification_missing",
                "goal_status": "partially_complete",
                "evidence_status": "collected",
                "verification_status": "missing",
            },
            strategy_guidance={
                "recommended_strategy": "verify_existing_changes",
                "focus": ["verification"],
                "avoid": ["repeat_same_write_without_verification"],
            },
        )

    return JudgeDecision(
        status="accepted",
        reason="Collected sufficient evidence",
        coverage_report={"evidence_count": evidence_count, "verification": verification or {}},
        diagnosis={
            "primary_gap": "resolved",
            "goal_status": "satisfied",
            "evidence_status": "sufficient",
            "verification_status": _verification_status(payload, goal_envelope),
        },
        strategy_guidance={
            "recommended_strategy": "finalize_response",
            "focus": ["final_response"],
        },
    )
