from __future__ import annotations

from typing import Any, Callable

from agent_runtime_framework.api.responses.common_payloads import compact_text, trace_detail_for_action, with_router_trace

PayloadFn = Callable[[], dict[str, Any]]
ListPayloadFn = Callable[[], list[dict[str, Any]]]


def build_result_payload(
    result: Any,
    *,
    route_decision: dict[str, str] | None,
    session_payload: PayloadFn,
    plan_history_payload: ListPayloadFn,
    run_history_payload: ListPayloadFn,
    memory_payload: PayloadFn,
    context_payload: PayloadFn,
    workspace: str,
) -> dict[str, Any]:
    approval_request = None
    resume_token_id = None
    if result.approval_request is not None:
        approval_request = {
            "capability_name": result.approval_request.capability_name,
            "instruction": result.approval_request.instruction,
            "reason": result.approval_request.reason,
            "risk_class": result.approval_request.risk_class,
        }
    if result.resume_token is not None:
        resume_token_id = result.resume_token.token_id
    return {
        "status": result.status,
        "run_id": result.run_id,
        "plan_id": result.task.task_id,
        "final_answer": result.final_output,
        "execution_trace": with_router_trace(
            route_decision,
            [
                {
                    "name": "evaluator" if bool(action.metadata.get("from_evaluator")) else action.kind,
                    "status": action.status,
                    "detail": compact_text(trace_detail_for_action(action)),
                }
                for action in result.task.actions
            ],
        ),
        "approval_request": approval_request,
        "resume_token_id": resume_token_id,
        "session": session_payload(),
        "plan_history": plan_history_payload(),
        "run_history": run_history_payload(),
        "memory": memory_payload(),
        "context": context_payload(),
        "workspace": workspace,
    }
