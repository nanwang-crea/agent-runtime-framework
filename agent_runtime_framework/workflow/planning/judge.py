from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime_framework.workflow.llm.structured_output_repair import repair_structured_output
from agent_runtime_framework.workflow.llm.access import chat_json
from agent_runtime_framework.workflow.memory.views import build_judge_memory_view
from agent_runtime_framework.workflow.state.models import AgentGraphState, GoalEnvelope, JudgeDecision, build_agent_graph_execution_summary, normalize_aggregated_workflow_payload
from agent_runtime_framework.workflow.planning.prompts import build_judge_system_prompt


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


def _judge_context_payload(goal_envelope: GoalEnvelope, payload: dict[str, Any], graph_state: AgentGraphState) -> dict[str, Any]:
    return {
        "goal": goal_envelope.goal,
        "intent": goal_envelope.intent,
        "target_hints": list(goal_envelope.target_hints),
        "success_criteria": list(goal_envelope.success_criteria),
        "constraints": dict(goal_envelope.constraints),
        "current_iteration": graph_state.current_iteration,
        "aggregated_payload": payload,
        "execution_summary": build_agent_graph_execution_summary(graph_state),
        "judge_memory_view": build_judge_memory_view(graph_state),
    }


def _normalize_optional_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _normalize_model_judge_decision(raw: dict[str, Any]) -> JudgeDecision:
    status = str(raw.get("status") or "replan").strip().lower()
    if status not in {"accept", "accepted", "replan"}:
        status = "replan"
    normalized_status = "accepted" if status in {"accept", "accepted"} else "replan"
    return JudgeDecision(
        status=normalized_status,
        reason=str(raw.get("reason") or ""),
        missing_evidence=[str(item) for item in raw.get("missing_evidence", []) or [] if str(item).strip()],
        coverage_report=_normalize_optional_mapping(raw.get("coverage_report")),
        replan_hint=_normalize_optional_mapping(raw.get("replan_hint")),
        diagnosis=_normalize_optional_mapping(raw.get("diagnosis")),
        strategy_guidance=_normalize_optional_mapping(raw.get("strategy_guidance")),
        allowed_next_node_types=[str(item) for item in raw.get("allowed_next_node_types", []) or [] if str(item).strip()],
        blocked_next_node_types=[str(item) for item in raw.get("blocked_next_node_types", []) or [] if str(item).strip()],
        must_cover=[str(item) for item in raw.get("must_cover", []) or [] if str(item).strip()],
        planner_instructions=str(raw.get("planner_instructions") or ""),
    )


def _judge_contract_error(raw: dict[str, Any] | None) -> str | None:
    if not isinstance(raw, dict) or not raw:
        return "judge contract missing"
    status = str(raw.get("status") or "").strip().lower()
    if status not in {"accept", "accepted", "replan"}:
        return "judge contract missing valid status"
    if not str(raw.get("reason") or "").strip():
        return "judge contract missing reason"
    if status == "replan" and not [str(item).strip() for item in raw.get("allowed_next_node_types", []) or [] if str(item).strip()]:
        return "judge contract missing allowed_next_node_types"
    return None


def _guardrail_decision(
    goal_envelope: GoalEnvelope,
    payload: dict[str, Any],
    graph_state: AgentGraphState,
) -> JudgeDecision | None:
    judge_memory_view = build_judge_memory_view(graph_state)
    max_iterations = int(goal_envelope.constraints.get("max_iterations") or 0)
    if max_iterations and graph_state.current_iteration >= max_iterations:
        return JudgeDecision(
            status="replan",
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
            blocked_next_node_types=["final_response"],
            must_cover=["report remaining gaps"],
            planner_instructions="Do not finalize. Summarize current progress and remaining gaps.",
        )

    conflicts = [str(item) for item in payload.get("conflicts", []) or [] if str(item).strip()]
    conflicts.extend(_semantic_path_consistency_conflicts(payload, judge_memory_view))
    if conflicts:
        return JudgeDecision(
            status="replan",
            reason="Conflicting evidence needs to be resolved before answering",
            missing_evidence=["resolved conflict"],
            coverage_report={"conflicts": conflicts},
            diagnosis={
                "primary_gap": "conflicting_evidence",
                "goal_status": "conflicted",
                "verification_status": _verification_status(payload, goal_envelope),
            },
            strategy_guidance={
                "recommended_strategy": "resolve_conflict_before_answering",
                "focus": ["target_resolution", "compare evidence"],
            },
            allowed_next_node_types=["target_resolution", "plan_read", "chunked_file_read", "verification"],
            blocked_next_node_types=["final_response"],
            must_cover=["resolve conflicting evidence"],
            planner_instructions="Resolve the conflict before answering. Do not finalize.",
        )
    return None


def _record_repair(graph_state: AgentGraphState):
    def _recorder(event: dict[str, Any]) -> None:
        graph_state.repair_history.append(dict(event))

    return _recorder


def judge_progress(
    goal_envelope: GoalEnvelope,
    aggregated_payload: dict[str, Any] | None,
    graph_state: AgentGraphState,
    context: Any | None = None,
) -> JudgeDecision:
    payload = normalize_aggregated_workflow_payload(aggregated_payload)
    guardrail_decision = _guardrail_decision(goal_envelope, payload, graph_state)
    if guardrail_decision is not None:
        return guardrail_decision
    judge_payload = _judge_context_payload(goal_envelope, payload, graph_state)
    try:
        model_payload = chat_json(
            context,
            role="judge",
            system_prompt=build_judge_system_prompt(),
            payload=judge_payload,
            max_tokens=600,
            temperature=0.0,
        )
        contract_error = _judge_contract_error(model_payload if isinstance(model_payload, dict) else None)
    except Exception as exc:
        model_payload = None
        contract_error = f"{type(exc).__name__}: {exc}"
    if contract_error is None and isinstance(model_payload, dict):
        return _normalize_model_judge_decision(model_payload)
    if contract_error is not None:
        repaired = repair_structured_output(
            context,
            role="judge",
            contract_kind="judge_contract",
            required_fields=["status", "reason", "allowed_next_node_types"],
            original_output=model_payload,
            validation_error=contract_error,
            request_payload=judge_payload,
            extra_instructions="status must be either accept or replan. replan requires allowed_next_node_types.",
            on_record=_record_repair(graph_state),
        )
        if isinstance(repaired, dict) and _judge_contract_error(repaired) is None:
            return _normalize_model_judge_decision(repaired)
    return JudgeDecision(
        status="replan",
        reason="Judge model unavailable",
        missing_evidence=["judge routing decision"],
        coverage_report={"model_status": "unavailable"},
        diagnosis={
            "primary_gap": "judge_model_unavailable",
            "verification_status": _verification_status(payload, goal_envelope),
        },
        blocked_next_node_types=["final_response"],
        must_cover=["obtain a valid judge routing decision"],
        planner_instructions="Do not finalize while the judge model is unavailable.",
    )
